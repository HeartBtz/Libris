import io
import json
import zipfile

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.engines.translation.critique_queue import matches_acceptance, validate_accepted_revision
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.main import app
from app.models import Issue, Job, Memory, Project, RequestLog, Segment
from app.providers.llm import ProviderContentRefused, llm
from app.providers.refusals import refusal_text
from app.schemas import ChapterAnalysis, TranslationResult


def test_queued_critique_matches_after_database_reload():
    acceptance = {"unit_id": "unit-1", "suggestion": "Corriger ceci."}
    reloaded = {"unit_id": "unit-1", "suggestion": "Corriger ceci.", "queued": True}

    assert reloaded is not acceptance
    assert matches_acceptance(reloaded, acceptance)


def test_queued_critique_rejects_editorial_instruction_as_translation():
    source = [{"id": "unit-1", "text": "⟦t0⟧Source text.⟦/t0⟧"}]
    result = TranslationResult(
        units=[{"id": "unit-1", "text": "⟦t0⟧Remplacer par « Texte corrigé. »⟦/t0⟧"}]
    )

    with pytest.raises(ValueError, match="consigne éditoriale"):
        validate_accepted_revision(source, result)


@respx.mock
async def test_refusal_blocks_without_fabricating_analysis_or_retrying(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, pid), "analyze", {})
        db.commit()
        jid = job.id
    route = respx.post("https://llm.test/v1/chat/completions").respond(
        200,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": "", "refusal": "Content policy refusal"}}
            ]
        },
    )
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "blocked" and job.stop_reason == "content_refusal"
        assert db.scalar(select(Memory.id).where(Memory.project_id == pid, Memory.kind == "analysis")) is None
        segment = db.get(Segment, job.checkpoint["segment_id"])
        assert segment.source and not segment.translation and segment.status == "refused"
        assert db.scalar(select(RequestLog)).status == "refused"
    assert route.call_count == 1 and claim() is None


@respx.mock
async def test_translation_skips_each_passage_after_two_refusals_and_continues(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        job = enqueue(db, project, "translate", {})
        total = db.scalar(select(func.count()).select_from(Segment).where(Segment.project_id == pid))
        db.commit()
        jid = job.id
    route = respx.post("https://llm.test/v1/chat/completions").respond(
        200,
        json={
            "choices": [
                {"finish_reason": "stop", "message": {"content": "", "refusal": "Content policy refusal"}}
            ]
        },
    )

    await execute(*claim())

    with SessionLocal() as db:
        job = db.get(Job, jid)
        refused = db.scalar(
            select(func.count()).select_from(Segment).where(
                Segment.project_id == pid, Segment.status == "refused"
            )
        )
        issues = db.scalar(
            select(func.count()).select_from(Issue).where(
                Issue.project_id == pid, Issue.code == "content_refusal"
            )
        )
        assert job.status == "completed"
        assert refused == total and issues == total
    assert route.call_count == total * 2


@respx.mock
async def test_structured_refusal_is_not_accepted_as_a_summary(seeded):
    pid, _, provider = seeded
    payload = {"summary": "I'm sorry, but I can't analyze this content due to policy."}
    respx.post("https://llm.test/v1/chat/completions").respond(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
    )
    with pytest.raises(ProviderContentRefused):
        await llm.complete(
            project_id=pid,
            provider_id=provider,
            operation="chapter_analysis",
            messages=[{"role": "user", "content": "Analyze"}],
            response_model=ChapterAnalysis,
        )
    assert refusal_text("I’m sorry, but I can’t translate this content.")
    assert not refusal_text("“I cannot translate this,” the character said.")


def test_explicit_original_retention_and_human_analysis_keep_coverage_honest(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        sid = segment.id
        segment.status = "refused"
        db.commit()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        saved = client.post(f"/api/segments/{sid}/retain-source", json={"revision": 0})
        assert saved.status_code == 200 and saved.json()["retained_source"]
        assert not saved.json()["validated"]
        coverage = client.get(f"/api/projects/{pid}/coverage").json()
        assert (
            coverage["translated"] == 0
            and sid in coverage["retained_source"]
            and sid in coverage["analysis_missing"]
        )
        assert client.get(f"/api/projects/{pid}/export/epub").status_code == 409
        partial = client.get(f"/api/projects/{pid}/export/epub?allow_source=true")
        assert partial.status_code == 200 and zipfile.is_zipfile(io.BytesIO(partial.content))
        assert "partial" in partial.headers["content-disposition"]
        assert client.put(f"/api/segments/{sid}/analysis", json={"summary": ""}).status_code == 422
        manual = client.put(
            f"/api/segments/{sid}/analysis", json={"summary": "Human notes about the narrative events."}
        )
        assert manual.status_code == 200
        assert sid not in client.get(f"/api/projects/{pid}/coverage").json()["analysis_missing"]


def test_accepting_ai_critique_applies_protected_human_correction(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        segment.translated_units = [
            {"id": unit["id"], "text": unit["text"]} for unit in segment.units
        ]
        segment.translation = "\n\n".join(unit["text"] for unit in segment.units)
        segment.status = "check"
        segment.critique = [
            {
                "unit_id": segment.units[0]["id"],
                "category": "grammar",
                "severity": "warning",
                "description": "La formulation est maladroite.",
                "suggestion": "Écrire : « Texte corrigé. »",
            }
        ]
        segment.uncertainties = ["Information narrative non bloquante."]
        sid = segment.id
        db.commit()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.post(f"/api/segments/{sid}/critique/0/accept", json={"revision": 0})

    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["human"] and not saved["validated"] and saved["status"] == "ok"
    assert "Texte corrigé." in saved["translated_units"][0]["text"]
    assert saved["critique"] == []
    assert saved["uncertainties"] == ["Information narrative non bloquante."]


def test_accepting_explicit_replacement_preserves_epub_markers(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segment = next(
            item
            for item in db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
            if any("Silver Tower" in unit["text"] for unit in item.units)
        )
        source = next(unit for unit in segment.units if "Silver Tower" in unit["text"])
        segment.translated_units = [{"id": source["id"], "text": source["text"]}]
        segment.translation = segment.translated_units[0]["text"]
        segment.status = "check"
        segment.critique = [
            {
                "unit_id": source["id"],
                "category": "grammar",
                "severity": "warning",
                "description": "A correction is available.",
                "suggestion": "Remplacer « Silver Tower » par « Tour d'argent ».",
            }
        ]
        sid = segment.id
        db.commit()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.post(f"/api/segments/{sid}/critique/0/accept", json={"revision": 0})

    assert response.status_code == 200, response.text
    assert response.json()["translated_units"][0]["text"] == source["text"].replace(
        "Silver Tower", "Tour d'argent"
    )


def test_accepting_ambiguous_critique_queues_ai_work(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        segment.translated_units = [{"id": unit["id"], "text": unit["text"]} for unit in segment.units]
        segment.translation = "\n\n".join(unit["text"] for unit in segment.translated_units)
        segment.status = "check"
        segment.critique = [
            {
                "unit_id": segment.units[0]["id"],
                "category": "style",
                "severity": "warning",
                "description": "A rewrite needs context.",
                "suggestion": "Rendre cette formulation plus naturelle.",
            }
        ]
        sid = segment.id
        db.commit()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.post(f"/api/segments/{sid}/critique/0/accept", json={"revision": 0})

    assert response.status_code == 200, response.text
    assert response.json()["queued"] is True
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.project_id == pid))
        assert job.operation == "accept_critiques"
        assert job.options["critique_acceptances"][0]["segment_id"] == sid
        assert db.get(Segment, sid).critique[0]["queued"] is True


def test_rejecting_ai_critique_keeps_and_protects_current_translation(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        translated = [{"id": unit["id"], "text": unit["text"]} for unit in segment.units]
        segment.translated_units = translated
        segment.translation = "\n\n".join(unit["text"] for unit in translated)
        segment.status = "check"
        segment.critique = [
            {
                "unit_id": segment.units[0]["id"],
                "category": "style",
                "severity": "warning",
                "description": "Une autre formulation est possible.",
                "suggestion": "Écrire : « Autre formulation. »",
            }
        ]
        sid, original = segment.id, segment.translation
        db.commit()

    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.post(f"/api/segments/{sid}/critique/0/reject", json={"revision": 0})

    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["translation"] == original
    assert saved["human"] and not saved["validated"] and saved["status"] == "ok"
    assert saved["critique"] == []
