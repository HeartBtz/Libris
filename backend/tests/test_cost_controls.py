"""Cost per passage: stable prompt prefix, review without revision, fused review, passage size."""

import json
import re

import httpx
import respx
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.builder import build_context
from app.engines.context.prefix import ordered, tier
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import Job, Project, RequestLog, Segment
from app.schemas import ProjectConfig

SECTION = re.compile(r"^<([A-Z_]+)>$", re.M)


def section_names(messages: list[dict]) -> list[str]:
    return SECTION.findall(messages[-1]["content"])


def target(body: dict) -> list[dict]:
    text = "\n".join(m["content"] for m in body["messages"])
    return json.loads(re.search(r"<TARGET_TEXT>\n(.*?)\n</TARGET_TEXT>", text, re.S)[1])


def reply(result: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        },
    )


def issue(unit_id: str) -> dict:
    return {
        "unit_id": unit_id,
        "category": "terminology",
        "severity": "warning",
        "description": "Name drift.",
        "suggestion": "Keep the name.",
    }


def mock(review_finds: bool, fused_answer=None):
    """Translation: 'FR ' prefix; review: one issue on the first unit if asked; revision: 'REV '."""

    def respond(request):
        body = json.loads(request.content)
        name = body.get("response_format", {}).get("json_schema", {}).get("name")
        units = target(body)
        if name == "ReviewResult":
            return reply({"issues": [issue(units[0]["id"])] if review_finds else []})
        if name == "ReviewRevisionResult":
            if fused_answer is not None:
                return reply(fused_answer(units))
            revised = [{"id": u["id"], "text": "REV " + u["text"]} for u in units]
            return reply(
                {
                    "issues": [issue(units[0]["id"])] if review_finds else [],
                    "units": revised if review_finds else [],
                    "new_terms": [],
                    "events": [],
                    "uncertainties": [],
                }
            )
        prefix = "REV " if "<REVIEW>" in body["messages"][-1]["content"] else "FR "
        translated = [{"id": u["id"], "text": prefix + u["text"]} for u in units]
        return reply({"units": translated, "new_terms": [], "events": [], "uncertainties": []})

    return respond


async def translate(pid: str) -> Job:
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, pid), "translate", {"final_review": False})
        db.commit()
        jid = job.id
    await execute(*claim())
    with SessionLocal() as db:
        return db.get(Job, jid)


def configure(pid: str, **values) -> None:
    with SessionLocal() as db:
        project = db.get(Project, pid)
        config = values.pop("config", None)
        for key, value in values.items():
            setattr(project, key, value)
        if config is not None:
            project.config = {**project.config, **config}
        db.commit()


def operations(pid: str) -> list[str]:
    with SessionLocal() as db:
        return [
            log.operation
            for log in db.scalars(
                select(RequestLog)
                .where(RequestLog.project_id == pid, RequestLog.cached.is_(False))
                .order_by(RequestLog.created_at)
            )
        ]


def test_sections_are_ordered_from_book_to_passage():
    names = ["PREVIOUS_CONTEXT", "TARGET_TEXT", "GLOSSARY", "REVIEW", "EDITORIAL_BOOK_CONTEXT", "USER_RULES",
             "OPENVIKING_MEMORY", "CHAPTER_STATE", "NEXT_CONTEXT", "CURRENT_TRANSLATION"]  # fmt: skip
    result = [name for name, _ in ordered([(name, "") for name in names])]
    assert result == [
        "EDITORIAL_BOOK_CONTEXT", "USER_RULES", "GLOSSARY", "OPENVIKING_MEMORY", "CHAPTER_STATE",
        "PREVIOUS_CONTEXT", "NEXT_CONTEXT", "REVIEW", "CURRENT_TRANSLATION", "TARGET_TEXT",
    ]  # fmt: skip
    assert tier("SOMETHING_NEW") < tier("TARGET_TEXT")


async def test_consecutive_passages_share_the_book_prefix(seeded):
    pid, _, _ = seeded
    configure(pid, bible={"summary": "A long mystery about a pendant. " * 40}, bible_validated=True)
    with SessionLocal() as db:
        ids = list(db.scalars(select(Segment.id).where(Segment.project_id == pid).order_by(Segment.position)))
    first, second = [await build_context(pid, sid, "translation") for sid in ids[1:3]]
    for built in (first, second):
        names = section_names(built.messages)
        assert names[-1] == "TARGET_TEXT"
        assert names.index("EDITORIAL_BOOK_CONTEXT") < names.index("PREVIOUS_CONTEXT")
    a, b = first.messages[-1]["content"], second.messages[-1]["content"]
    book = a.index("</EDITORIAL_BOOK_CONTEXT>")
    assert a[:book] == b[:book] and book > 1000
    assert first.messages[0] == second.messages[0]


@respx.mock
async def test_no_revision_call_when_the_review_finds_nothing(seeded):
    pid, _, _ = seeded
    configure(pid, quality="high")
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock(review_finds=False))
    job = await translate(pid)
    assert job.status == "completed", job.error
    calls = operations(pid)
    assert "translation_review" in calls and "translation_revision" not in calls


@respx.mock
async def test_fused_review_revises_in_one_call(seeded):
    pid, _, _ = seeded
    configure(pid, quality="high", config={"review_mode": "fused"})
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock(review_finds=True))
    job = await translate(pid)
    assert job.status == "completed", job.error
    calls = operations(pid)
    assert set(calls) == {"translation", "review_revision"}
    with SessionLocal() as db:
        # The navigation passage refuses text outside its links: the mock's prefix fails it, as without fusion.
        segments = [s for s in db.scalars(select(Segment).where(Segment.project_id == pid)) if s.translation]
        assert len(segments) >= 5
        assert all(s.stage == "done" and s.critique for s in segments)
        assert all(u["text"].startswith("REV ") for s in segments for u in s.translated_units)


@respx.mock
async def test_fused_review_without_findings_keeps_the_translation(seeded):
    pid, _, _ = seeded
    configure(pid, quality="high", config={"review_mode": "fused"})
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock(review_finds=False))
    job = await translate(pid)
    assert job.status == "completed", job.error
    with SessionLocal() as db:
        segments = [s for s in db.scalars(select(Segment).where(Segment.project_id == pid)) if s.translation]
        assert len(segments) >= 5
        assert all(s.status == "ok" and not s.critique for s in segments)
        assert all(u["text"].startswith("FR ") for s in segments for u in s.translated_units)


@respx.mock
async def test_fused_review_falls_back_to_two_calls_after_invalid_answers(seeded):
    pid, _, _ = seeded
    configure(pid, quality="high", config={"review_mode": "fused"})

    def inconsistent(units):
        # Findings without the corrected units: rejected every time.
        return {
            "issues": [issue(units[0]["id"])],
            "units": [],
            "new_terms": [],
            "events": [],
            "uncertainties": [],
        }

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock(True, inconsistent))
    job = await translate(pid)
    assert job.status == "completed", job.error
    calls = operations(pid)
    assert {"translation_review", "translation_revision"} <= set(calls)
    with SessionLocal() as db:
        segments = [s for s in db.scalars(select(Segment).where(Segment.project_id == pid)) if s.translation]
        assert len(segments) >= 5
        assert all(s.stage == "done" for s in segments)
        assert all(u["text"].startswith("REV ") for s in segments for u in s.translated_units)


def test_project_configuration_accepts_passage_size_and_review_mode():
    config = ProjectConfig(title="x", passage_max_chars=6000, review_mode="fused")
    assert (config.passage_max_chars, config.review_mode) == (6000, "fused")


def test_the_estimate_follows_the_review_mode(seeded):
    from app.api.estimates import plan

    pid, _, _ = seeded
    configure(pid, quality="high")
    with SessionLocal() as db:
        separate = [step.operation for step in plan(db, db.get(Project, pid), "translate")[0]]
    configure(pid, config={"review_mode": "fused"})
    with SessionLocal() as db:
        fused = {step.operation: step.count for step in plan(db, db.get(Project, pid), "translate")[0]}
    assert {"translation_review", "translation_revision"} <= set(separate)
    assert (
        "translation_review" not in fused
        and fused["review_revision"] > 0
        and fused["translation_revision"] == 0
    )
