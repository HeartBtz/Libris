from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.translation import critique_queue, pipeline, repair
from app.jobs import worker
from app.jobs.execution import execution
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.main import app
from app.models import Job, Project, Provider, Segment
from app.providers.llm import InvalidResponseExhausted, ProviderUnavailable
from app.schemas import TranslationResult


async def test_repair_checkpoints_successful_batches(seeded, monkeypatch):
    with SessionLocal() as db:
        project = db.get(Project, seeded[0])
        segment = db.scalar(select(Segment).where(Segment.project_id == project.id))
        segment.units = [{"id": f"u{i}", "text": "Hello"} for i in range(9)]
        job = enqueue(db, project, "translate", {})
        db.commit()
    scope = claim()
    token = execution.set(scope)
    calls = []
    failed = False

    async def context(*args, **kwargs):
        return SimpleNamespace(messages=kwargs["extra"]["TARGET_TEXT"], inspector={})

    async def answer(**kwargs):
        nonlocal failed
        ids = [u["id"] for u in kwargs["messages"]]
        calls.append(ids)
        if ids[0] == "u4" and not failed:
            failed = True
            raise ProviderUnavailable("temporary")
        result = TranslationResult(units=[{"id": uid, "text": "Bonjour"} for uid in ids])
        kwargs["validator"](result)
        return result

    monkeypatch.setattr(repair, "build_context", context)
    monkeypatch.setattr(repair.llm, "complete", answer)
    try:
        with pytest.raises(ProviderUnavailable):
            await repair.repair_translation(project, segment, "translation", job, {}, [])
        result = await repair.repair_translation(project, segment, "translation", job, {}, [])
        assert [u.id for u in result.units] == [u["id"] for u in segment.units]
        assert sum(ids[0] == "u0" for ids in calls) == 1
    finally:
        execution.reset(token)


async def test_accepted_critique_waits_on_provider_outage(seeded, monkeypatch):
    from app.api.segments import queue_critique

    with SessionLocal() as db:
        project = db.get(Project, seeded[0])
        segment = db.scalar(select(Segment).where(Segment.project_id == project.id))
        segment.translated_units = [{"id": u["id"], "text": "Bonjour"} for u in segment.units]
        critique = {"unit_id": segment.units[0]["id"], "suggestion": "Improve style"}
        segment.critique = [critique]
        job = queue_critique(db, project, segment, critique, seeded[1])
        jid, sid = job.id, segment.id
        db.commit()

    async def context(*args, **kwargs):
        return SimpleNamespace(messages=[], inspector={})

    async def unavailable(**kwargs):
        raise ProviderUnavailable("Codex unavailable")

    monkeypatch.setattr(critique_queue, "build_context", context)
    monkeypatch.setattr(critique_queue.llm, "complete", unavailable)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "waiting"
        assert db.get(Segment, sid).critique[0]["queued"] is True


def test_recovery_selection_and_completion(seeded):
    with SessionLocal() as db:
        db.get(Project, seeded[0]).bible = {"summary": "Synthetic analysis"}
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        pid = seeded[0]
        report = client.get(f"/api/projects/{pid}/completion").json()
        assert not report["coverage_complete"] and report["missing"] == report["total"]
        sid = report["recovery"][0]["id"]
        assert (
            client.post(
                f"/api/projects/{pid}/jobs", json={"operation": "translate", "segment_ids": ["foreign"]}
            ).status_code
            == 422
        )
        response = client.post(
            f"/api/projects/{pid}/jobs",
            json={"operation": "translate", "segment_ids": [sid], "continue_pipeline": True},
        )
        assert response.status_code == 202, response.text
        with SessionLocal() as db:
            job = db.get(Job, response.json()["id"])
            assert job.options["force"] and job.options["segment_ids"] == [sid]
            assert job.options["continue_pipeline"]


async def test_full_translation_stops_before_review_when_recovery_is_required(seeded, monkeypatch):
    with SessionLocal() as db:
        project = db.get(Project, seeded[0])
        project.quality = "high"
        segments = list(db.scalars(select(Segment).where(Segment.project_id == project.id)))
        for segment in segments:
            segment.translation = "Traduit"
            segment.translated_units = [{"id": unit["id"], "text": "Traduit"} for unit in segment.units]
            segment.stage = "done"
            segment.status = "ok"
        segments[0].translation = ""
        segments[0].translated_units = []
        segments[0].status = "error"
        job = enqueue(db, project, "translate", {})
        jid = job.id
        db.commit()
    claimed = claim()
    assert claimed and claimed[0] == jid

    async def unexpected_review(*args, **kwargs):
        raise AssertionError("Consistency review must wait for passage recovery")

    monkeypatch.setattr(pipeline, "consistency", unexpected_review)
    await pipeline.translate(job, claimed[1])
    with SessionLocal() as db:
        current = db.get(Job, jid)
        assert current.checkpoint["step"] == "recovery_required"
        assert current.checkpoint["recovery_required"] == 1


async def test_analysis_job_continues_with_translation(seeded, monkeypatch):
    with SessionLocal() as db:
        job = enqueue(
            db,
            db.get(Project, seeded[0]),
            "analyze",
            {"continue_pipeline": True, "automatic_recovery": True, "full_review": True},
        )
        jid = job.id
        db.commit()
    calls = []

    async def analyze(*args):
        calls.append("analyze")

    async def translate(*args):
        calls.append("translate")

    monkeypatch.setattr(worker, "analyze", analyze)
    monkeypatch.setattr(worker, "translate", translate)
    await worker.execute(*claim())

    assert calls == ["analyze", "translate"]
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"


@pytest.mark.parametrize("recovered", [True, False])
async def test_automatic_pipeline_retries_missing_segments_once(seeded, monkeypatch, recovered):
    with SessionLocal() as db:
        project = db.get(Project, seeded[0])
        segments = list(db.scalars(select(Segment).where(Segment.project_id == project.id)))
        for segment in segments:
            segment.translation = "Traduit"
            segment.translated_units = [
                {"id": unit["id"], "text": "Traduit"} for unit in segment.units
            ]
            segment.stage, segment.status = "done", "ok"
        target = segments[0]
        target.translation, target.translated_units = "", []
        target.stage, target.status = "pending", "pending"
        job = enqueue(
            db,
            project,
            "translate",
            {"automatic_recovery": True, "continue_pipeline": True, "full_review": True},
        )
        jid, sid = job.id, target.id
        db.commit()
    calls = 0
    review_calls = 0

    async def translation(project, segment, operation, job, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1 or not recovered:
            raise InvalidResponseExhausted("invalid")
        return TranslationResult(
            units=[{"id": unit["id"], "text": "Récupéré"} for unit in segment.units]
        )

    async def review(*args):
        nonlocal review_calls
        review_calls += 1

    monkeypatch.setattr(pipeline, "translation_call", translation)
    monkeypatch.setattr("app.engines.translation.final_review.resolve_validations", review)
    claimed = claim()
    await pipeline.translate(job, claimed[1])

    assert calls == 2 and review_calls == 1
    with SessionLocal() as db:
        current = db.get(Job, jid)
        segment = db.get(Segment, sid)
        assert current.checkpoint["automatic_recovery_targets"] == [sid]
        assert current.checkpoint["automatic_recovery_completed"] is True
        assert current.checkpoint["automatic_recovery_remaining"] == (0 if recovered else 1)
        assert bool(segment.translation) is recovered
        assert segment.status == ("ok" if recovered else "error")
    if not recovered:
        await pipeline.translate(current, claimed[1])
        assert calls == 2


def test_recovery_provider_is_replaced_before_pipeline_continues(seeded):
    with SessionLocal() as db:
        project = db.get(Project, seeded[0])
        recovery_provider = Provider(
            name="Recovery only",
            base_url="https://recovery.test/v1",
            model="recovery-model",
        )
        db.add(recovery_provider)
        db.flush()
        job = enqueue(
            db,
            project,
            "translate",
            {
                "segment_ids": [db.scalar(select(Segment.id).where(Segment.project_id == project.id))],
                "provider_id": recovery_provider.id,
                "continue_pipeline": True,
            },
        )
        job.provider_id = recovery_provider.id
        project_provider_id = project.provider_id
        jid = job.id
        recovery_provider_id = recovery_provider.id
        db.commit()
    claimed = claim()
    assert claimed and claimed[0] == jid
    pipeline.restore_project_provider(jid, claimed[1], project_provider_id)
    with SessionLocal() as db:
        current = db.get(Job, jid)
        assert current.provider_id == project_provider_id
        assert "provider_id" not in current.options
        assert current.options["recovery_provider_id"] == recovery_provider_id


@pytest.mark.parametrize("valid", [True, False])
async def test_accept_advice_repairs_internal_markers(seeded, monkeypatch, valid):
    with SessionLocal() as db:
        s = db.scalar(select(Segment).where(Segment.project_id == seeded[0]))
        s.units = [{"id": "u1", "text": "The ⟦t0⟧light⟦/t0⟧ shines."}]
        s.translated_units = [{"id": "u1", "text": "La ⟦t0⟧lumière⟦/t0⟧ brille."}]
        s.translation = s.translated_units[0]["text"]
        s.critique = [
            {
                "unit_id": "u1",
                "suggestion": "Le feu brille.",
                "description": "Meaning",
                "category": "meaning",
                "severity": "warning",
            }
        ]
        sid = s.id
        db.commit()

    async def context(*args, **kwargs):
        assert len(kwargs["extra"]["TARGET_TEXT"]) == 1
        return SimpleNamespace(messages=[], inspector={})

    async def answer(**kwargs):
        result = TranslationResult(units=[{"id": "u1", "text": "Le ⟦t0⟧feu⟦/t0⟧ brille." if valid else "Le feu brille."}])
        kwargs["validator"](result)
        return result

    monkeypatch.setattr(critique_queue, "build_context", context)
    monkeypatch.setattr(critique_queue.llm, "complete", answer)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.post(f"/api/segments/{sid}/critique/0/accept", json={"revision": 0})
        assert response.status_code == 200, response.text
        assert response.json()["queued"]
    await execute(*claim())
    with SessionLocal() as db:
        saved = db.get(Segment, sid)
        expected = "Le ⟦t0⟧feu⟦/t0⟧ brille."
        assert saved.translation == expected
        assert saved.human
        assert not saved.critique
