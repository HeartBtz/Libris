from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import segments as segment_api
from app.db import SessionLocal
from app.engines.translation import repair
from app.jobs.execution import execution
from app.jobs.queue import claim, enqueue
from app.main import app
from app.models import Job, Project, Segment
from app.providers.llm import ProviderUnavailable
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
            f"/api/projects/{pid}/jobs", json={"operation": "translate", "segment_ids": [sid]}
        )
        assert response.status_code == 202, response.text
        with SessionLocal() as db:
            job = db.get(Job, response.json()["id"])
            assert job.options["force"] and job.options["segment_ids"] == [sid]


@pytest.mark.parametrize("valid", [True, False])
def test_accept_advice_repairs_internal_markers(seeded, monkeypatch, valid):
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

    monkeypatch.setattr(segment_api, "build_context", context)
    monkeypatch.setattr(segment_api.llm, "complete", answer)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.post(f"/api/segments/{sid}/critique/0/accept", json={"revision": 0})
        if not valid:
            assert response.status_code == 422
            with SessionLocal() as db:
                assert db.get(Segment, sid).translation == "La ⟦t0⟧lumière⟦/t0⟧ brille."
            return
        assert response.status_code == 200, response.text
        assert response.json()["translation"] == "Le ⟦t0⟧feu⟦/t0⟧ brille."
        assert response.json()["human"]
