"""Autopilot: an imported book reaches an output with no human action, whatever the model does."""

import json
import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.jobs.launch import launch
from app.jobs.queue import claim
from app.jobs.worker import execute
from app.main import app
from app.models import AutopilotDecision, Glossary, Issue, Job, Project, Provider, Segment
from app.schemas import FinalReviewResult, ReviewResult

TERMINAL = {"completed", "failed", "cancelled"}
WORDS = {"Chapter": "Chapitre", "One": "un", "Two": "deux", "Silver": "d’argent", "Tower": "Tour"}


def french(text: str) -> str:
    # Words only: markers and the whitespace of navigation entries stay where they are.
    return re.sub(r"[A-Za-z]+", lambda m: WORDS.get(m[0], m[0].lower() if m[0] != "Alice" else m[0]), text)


def reply(content: dict | str, finish: str = "stop") -> httpx.Response:
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": body}}]})


REFUSAL = httpx.Response(
    200, json={"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]}
)


def section(messages: list[dict], name: str):
    text = "\n".join(m["content"] for m in messages)
    found = re.search(rf"<{name}>\n(.*?)\n</{name}>", text, re.S)
    return json.loads(found[1]) if found else None


class Book:
    """A synthetic model pair. The primary refuses an analysis and a translation, then goes down for good
    when it meets the `poison` passage; the backup takes over but can never translate that passage."""

    def __init__(self, poison: set[str]):
        self.poison = poison
        self.down = False
        self.calls: list[tuple[str, str]] = []

    def primary(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        self.calls.append(("primary", name))
        target = section(body["messages"], "TARGET_TEXT") or []
        text = " ".join(unit["text"] for unit in target)
        if name == "TranslationResult" and any(unit["id"] in self.poison for unit in target):
            self.down = True
        if self.down:
            return httpx.Response(503, json={"error": "down"})
        if name == "ChapterAnalysis" and "brother" in text:
            return REFUSAL  # skipped, never blocking
        if name == "TranslationResult" and "stopped" in text:
            return REFUSAL  # recovered later by the backup provider
        return self.answer(name, body["messages"], target)

    def backup(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        self.calls.append(("backup", name))
        target = section(body["messages"], "TARGET_TEXT") or []
        if name == "TranslationResult" and any(unit["id"] in self.poison for unit in target):
            return reply({"units": []})  # invalid every time: this passage ends in the original
        return self.answer(name, body["messages"], target)

    def answer(self, name: str, messages: list[dict], target: list[dict]) -> httpx.Response:
        text = " ".join(unit["text"] for unit in target)
        if name == "ChapterAnalysis":
            return reply(
                {
                    "summary": "Alice explore la tour.",
                    "events": [],
                    "characters": [],
                    "terms": [
                        {
                            "source": "Silver Tower",
                            "translation": "Tour d’argent",
                            "category": "lieu",
                            "description": "",
                        },
                        {
                            "source": "Moonstone",
                            "translation": "Pierre de lune",
                            "category": "objet",
                            "description": "",
                        },
                    ],
                    "style_notes": [],
                    "relationships": [],
                }
            )
        if name == "BookOverview":
            return reply(
                {"summary": "Alice et Bob dans une tour.", **{k: "" for k in ("title", "author", "tone")}}
            )
        if name == "TranslationResult":
            units = [{"id": unit["id"], "text": french(unit["text"])} for unit in target]
            return reply({"units": units, "new_terms": [], "events": [], "uncertainties": []})
        if name == "ReviewResult":
            return reply(ReviewResult().model_dump())
        if name == "FinalReviewResult":
            current = section(messages, "CURRENT_TRANSLATION") or []
            if "Silver Tower" in text and "stopped" in text:
                # Never satisfied: the final review keeps a proposal open for the arbitration.
                unit = next(u for u in target if "stopped" in u["text"])
                return reply(
                    {
                        "decision": "revise",
                        "issues": [
                            {
                                "unit_id": unit["id"],
                                "category": "style",
                                "severity": "warning",
                                "description": "Verbe faible.",
                                "suggestion": "s’arrêta net",
                            }
                        ],
                        "uncertainties": [],
                        "explanation": "Une correction est possible.",
                        "search_queries": [],
                    }
                )
            assert current is not None
            return reply(FinalReviewResult(decision="accept", explanation="Correct.").model_dump())
        if name == "ArbitrationResult":
            proposals = section(messages, "PROPOSALS")
            current = section(messages, "CURRENT_TRANSLATION")
            critiques = [p for p in proposals if p["kind"] == "critique"]
            if critiques:
                unit = next(u for u in current if u["id"] == critiques[0]["unit_id"])
                return reply(
                    {
                        "decisions": [
                            {"id": p["id"], "accept": p["kind"] == "critique", "reason": "Arbitré."}
                            for p in proposals
                        ],
                        "units": [
                            {"id": unit["id"], "text": unit["text"].replace("stopped", "s’arrêta net")}
                        ],
                    }
                )
            return reply(
                {
                    "decisions": [
                        {"id": p["id"], "accept": False, "reason": "Sans objet."} for p in proposals
                    ],
                    "units": [],
                }
            )
        raise AssertionError(f"unexpected call {name}")


def add_backup(project_id: str) -> str:
    with SessionLocal() as db:
        backup = Provider(
            name="Backup",
            base_url="https://backup.test/v1",
            model="backup-model",
            capabilities={"supports_json_schema": True},
            context_window=64000,
        )
        db.add(backup)
        db.flush()
        project = db.get(Project, project_id)
        project.config = {**project.config, "fallback_provider_ids": [backup.id]}
        project.quality = "normal"
        db.commit()
        return backup.id


async def run(job_id: str, turns: int = 30) -> Job:
    for _ in range(turns):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job.status in TERMINAL:
                return job
            assert job.status in {"pending", "waiting"}, (job.status, job.stop_reason, job.error)
            job.next_attempt = 0  # the test does not wait for the backoff
            db.commit()
        item = claim()
        assert item, "the job must be claimable"
        await execute(*item)
    raise AssertionError("the job never ended")


@pytest.fixture
def fast_outages(monkeypatch):
    monkeypatch.setattr(settings(), "autopilot_outage_max_retries", 2)


@respx.mock
async def test_a_hostile_book_reaches_an_output_without_any_human_action(seeded, fast_outages):
    pid, _, primary_id = seeded
    backup_id = add_backup(pid)
    with SessionLocal() as db:
        poisoned = db.scalar(select(Segment).where(Segment.project_id == pid, Segment.position == 2))
        book = Book({unit["id"] for unit in poisoned.units})
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=book.primary)
    respx.post("https://backup.test/v1/chat/completions").mock(side_effect=book.backup)
    with SessionLocal() as db:
        job, reason = launch(db, db.get(Project, pid), "pipeline")
        assert job and job.options["autopilot"], reason
        db.commit()
        jid = job.id

    job = await run(jid)

    assert job.status == "completed", (job.error, job.stop_reason)
    report = job.result["autopilot"]
    assert report["outcome"] == "completed_with_residuals"
    assert 1 <= report["rounds"] <= settings().autopilot_max_rounds
    with SessionLocal() as db:
        segments = list(
            db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        )
        # Nothing is left for a person: every passage is translated or kept in the original with a reason.
        assert {s.status for s in segments} <= {"ok", "source_retained"}
        assert all(s.translation for s in segments)
        retained = [s for s in segments if s.retained_source]
        assert [s.id for s in retained] == [r["segment_id"] for r in report["residuals"]]
        assert len(retained) == 1 and retained[0].position == 2
        assert "Texte original conservé automatiquement" in report["residuals"][0]["reason"]
        # The passage the primary refused was recovered on the backup, then the proposal the final
        # review kept open on it was arbitrated and applied.
        opening = next(s for s in segments if "stopped" in s.source)
        assert opening.status == "ok" and not opening.retained_source and not opening.critique
        assert "s’arrêta net" in opening.translation
        assert not db.scalar(
            select(Issue.id).where(
                Issue.project_id == pid, Issue.resolved.is_(False), Issue.code != "source_retained"
            )
        )
        # Glossary proposals decided: the term the book uses twice is kept, the imagined one is not.
        terms = {g.source: g.accepted for g in db.scalars(select(Glossary).where(Glossary.project_id == pid))}
        assert terms == {"Silver Tower": True}
        assert db.get(Project, pid).bible_validated is True  # six passages of seven analysed
        decisions = list(db.scalars(select(AutopilotDecision).where(AutopilotDecision.project_id == pid)))
        actions = {(d.stage, d.kind, d.action) for d in decisions}
        assert ("analysis", "chapter_analysis", "skipped") in actions
        assert ("provider", "outage", "fallback_provider") in actions
        assert ("recovery", "failed_passage", "recovered") in actions
        assert ("recovery", "failed_passage", "source_retained") in actions
        assert ("arbitration", "critique", "applied") in actions
        assert ("memory", "glossary_term", "rejected") in actions
        assert ("report", "job", "completed_with_residuals") in actions
        assert all(d.reason for d in decisions)
        # The job moved to the backup provider after the bounded outage.
        assert job.provider_id == backup_id and db.get(Project, pid).provider_id == primary_id
    assert ("backup", "ArbitrationResult") in book.calls

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        body = client.get(f"/api/projects/{pid}/autopilot", params={"limit": 5}).json()
        assert body["report"]["outcome"] == "completed_with_residuals" and body["report"]["job_id"] == jid
        assert len(body["decisions"]["items"]) == 5 and body["decisions"]["total"] == len(decisions)
        stage = client.get(f"/api/projects/{pid}/autopilot", params={"stage": "provider"}).json()
        assert {item["action"] for item in stage["decisions"]["items"]} == {"fallback_provider"}


@respx.mock
async def test_no_provider_left_ends_the_job_failed_with_its_reason(seeded, fast_outages):
    pid = seeded[0]
    add_backup(pid)
    respx.post("https://llm.test/v1/chat/completions").respond(503, json={"error": "down"})
    respx.post("https://backup.test/v1/chat/completions").respond(503, json={"error": "down"})
    with SessionLocal() as db:
        job, _ = launch(db, db.get(Project, pid), "pipeline")
        db.commit()
        jid = job.id

    job = await run(jid)

    assert job.status == "failed" and job.stop_reason == "providers_exhausted"
    assert job.result["autopilot"]["outcome"] == "failed"
    assert "aucun autre fournisseur" in job.result["autopilot"]["reason"]
    with SessionLocal() as db:
        assert db.get(Project, pid).status == "failed"


def test_launches_are_autopilot_by_default_and_can_opt_out(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        db.get(Project, pid).bible = {"summary": "Analysé"}
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        started = client.post(f"/api/projects/{pid}/jobs", json={"operation": "translate"})
        assert started.status_code == 202, started.text
        assert started.json()["options"]["autopilot"] is True
        client.post(f"/api/jobs/{started.json()['id']}/cancel")
        with SessionLocal() as db:
            db.get(Job, started.json()["id"]).status = "cancelled"
            db.commit()
        manual = client.post(f"/api/projects/{pid}/jobs", json={"operation": "translate", "autopilot": False})
        assert "autopilot" not in manual.json()["options"]
        with SessionLocal() as db:
            db.get(Job, manual.json()["id"]).status = "cancelled"
            db.commit()
        # A targeted retranslation is a person's own request: never autopiloted.
        sid = client.get(f"/api/projects/{pid}/completion").json()["recovery"][0]["id"]
        scoped = client.post(f"/api/projects/{pid}/jobs", json={"operation": "translate", "segment_id": sid})
        assert "autopilot" not in scoped.json()["options"]


@respx.mock
async def test_convergence_is_bounded_and_settles_what_stays_open(seeded, monkeypatch):
    """The arbitration is refused every time: the loop stops after AUTOPILOT_MAX_ROUNDS and closes the
    open points on the current translation, with the reason, instead of leaving them to a person."""
    monkeypatch.setattr(settings(), "autopilot_max_rounds", 2)
    pid = seeded[0]
    book = Book(set())

    def model(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] == "ArbitrationResult":
            return REFUSAL
        return book.backup(request)

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=model)
    with SessionLocal() as db:
        job, _ = launch(db, db.get(Project, pid), "pipeline")
        db.commit()
        jid = job.id

    job = await run(jid)

    assert job.status == "completed"
    assert job.result["autopilot"] == {"outcome": "completed", "rounds": 2, "residuals": [], "reason": None}
    with SessionLocal() as db:
        statuses = set(db.scalars(select(Segment.status).where(Segment.project_id == pid)))
        assert statuses == {"ok"}
        decisions = list(db.scalars(select(AutopilotDecision).where(AutopilotDecision.job_id == jid)))
        deferred = [d for d in decisions if d.action == "deferred"]
        settled = [d for d in decisions if d.kind == "open_points"]
        assert len(deferred) == 2  # one refused arbitration per round, never more
        assert len(settled) == 1 and "Nombre maximal de tours atteint (2)" in settled[0].reason
        assert "Verbe faible" in settled[0].reason
    arbitrations = [call for call in book.calls if call[1] == "ArbitrationResult"]
    assert arbitrations == []  # refused before reaching the backup's answers


def running_job(project_id: str, options: dict | None = None) -> tuple[Job, str]:
    from app.jobs.queue import enqueue

    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, project_id), "translate", {"autopilot": True, **(options or {})})
        db.commit()
        jid = job.id
    owner = claim()[1]
    with SessionLocal() as db:
        return db.get(Job, jid), owner


def test_series_identities_and_stale_chapters_are_decided_with_their_thresholds(seeded):
    from app.engines.autopilot.memory import decide_identities, decide_stale_chapters
    from app.jobs import segment_state as state
    from app.models import Chapter, Entity, Series, SeriesEntity, SeriesEntityLink

    pid, uid, _ = seeded
    with SessionLocal() as db:
        series = Series(owner_id=uid, name="Saga", normalized_name="saga", bible={})
        db.add(series)
        db.flush()
        near = SeriesEntity(
            series_id=series.id, name="Robert", category="character", aliases=["Bob"], data={"gender": "male"}
        )
        far = SeriesEntity(
            series_id=series.id,
            name="Bobby Smith",
            category="character",
            aliases=["Bob", "B."],
            data={"gender": "female"},
        )
        twin_a = SeriesEntity(series_id=series.id, name="Ann", category="character", aliases=["Annie"])
        twin_b = SeriesEntity(series_id=series.id, name="Anna", category="character", aliases=["Annie"])
        bob = Entity(
            project_id=pid, name="Bob", category="character", data={"aliases": ["Robert"], "gender": "male"}
        )
        annie = Entity(project_id=pid, name="Annie", category="character", data={})
        db.add_all([near, far, twin_a, twin_b, bob, annie])
        db.flush()
        for entity, candidates in ((bob, (near, far)), (annie, (twin_a, twin_b))):
            for candidate in candidates:
                db.add(
                    SeriesEntityLink(
                        series_entity_id=candidate.id,
                        entity_id=entity.id,
                        project_id=pid,
                        status="proposed",
                        confidence=0.5,
                    )
                )
        chapters = list(
            db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.position))
        )
        chapters[0].context_stale = chapters[1].context_stale = True
        db.commit()
        first_chapter, second_chapter = chapters[0].id, chapters[1].id
        first_segments = list(db.scalars(select(Segment.id).where(Segment.chapter_id == first_chapter)))
    job, owner = running_job(pid)
    with SessionLocal() as db:
        state.mark_all(db, job.id, state.FINISHED, first_segments)
        db.commit()

    decide_identities(job, owner)
    decide_stale_chapters(job, owner)

    with SessionLocal() as db:
        links = {
            (link.entity_id, link.series_entity_id): link.status
            for link in db.scalars(select(SeriesEntityLink))
        }
        assert links[(bob.id, near.id)] == "linked" and links[(bob.id, far.id)] == "rejected"
        # Two identities equally likely: none is chosen, the character stays this volume's own.
        assert links[(annie.id, twin_a.id)] == links[(annie.id, twin_b.id)] == "rejected"
        assert db.get(Chapter, first_chapter).context_stale is False
        assert db.get(Chapter, second_chapter).context_stale is True  # not translated again by this job
        kinds = [
            (d.kind, d.action)
            for d in db.scalars(select(AutopilotDecision).where(AutopilotDecision.job_id == job.id))
        ]
        assert sorted(kinds) == sorted(
            [
                ("series_identity", "linked"),
                ("series_identity", "rejected"),
                ("context_stale", "cleared"),
                ("context_stale", "kept"),
            ]
        )
        # A later refresh of the series keeps these decisions.
        from app.engines.series.bible import link_characters

        link_characters(db, db.get(Series, series.id), db.get(Project, pid))
        db.flush()
        assert db.get(SeriesEntityLink, next(
            link.id for link in db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.entity_id == annie.id))
        )).status == "rejected"  # fmt: skip


async def test_refused_consistency_samples_context_plans_and_reviews_are_skipped(seeded, monkeypatch):
    from app.engines.translation import pipeline
    from app.providers.llm import ProviderContentRefused, llm

    pid = seeded[0]
    job, owner = running_job(pid, {"deep": True})
    samples = [{"key": "k1", "payload": {"subject": {"source": "Alice"}}, "occurrences": 2, "mapping": {}}]
    monkeypatch.setattr(pipeline, "_consistency_samples", lambda job: (db_project(pid), samples))

    async def refuse(**kwargs):
        raise ProviderContentRefused("Refus explicite du provider ou filtrage du contenu.")

    monkeypatch.setattr(llm, "complete", refuse)
    await pipeline.consistency(job, owner)
    with SessionLocal() as db:
        from app.jobs import segment_state as state

        assert state.batches(db, job.id, state.CONSISTENCY) == {"k1": {}}
        decision = db.scalar(select(AutopilotDecision).where(AutopilotDecision.kind == "consistency_sample"))
        assert decision.action == "skipped" and "Alice" in decision.reason
        segment = db.scalar(select(Segment).where(Segment.project_id == pid, Segment.position == 1))
        db.get(Project, pid).quality = "normal"
        db.commit()

    async def translation(project, segment, operation, job, *args, **kwargs):
        from app.schemas import TranslationResult

        return TranslationResult(units=[{"id": u["id"], "text": french(u["text"])} for u in segment.units])

    # The context plan and the review are refused: the passage is translated anyway, with standard context.
    monkeypatch.setattr(pipeline, "translation_call", translation)
    await pipeline.translate_passage(job, owner, segment.id, False)
    with SessionLocal() as db:
        done = db.get(Segment, segment.id)
        assert done.stage == "done" and done.translation and done.status in {"ok", "check"}
        skipped = {
            d.kind
            for d in db.scalars(select(AutopilotDecision).where(AutopilotDecision.segment_id == segment.id))
        }
        assert skipped == {"context_planner", "translation_review"}


def db_project(pid: str) -> Project:
    with SessionLocal() as db:
        return db.get(Project, pid)
