from types import SimpleNamespace

import pytest
import respx
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.engines.translation import final_review, pipeline
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import Issue, Job, Project, Segment
from app.providers.llm import ProviderUnavailable
from app.schemas import FinalReviewResult, TranslationResult


def prepare(pid, human=False):
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        segment.translated_units = [{"id": unit["id"], "text": "Le retour"} for unit in segment.units]
        segment.translation = "Le retour"
        segment.human, segment.status, segment.stage = human, "check", "done"
        segment.critique = [
            {
                "unit_id": segment.units[0]["id"],
                "category": "style",
                "severity": "warning",
                "description": "Old issue",
                "suggestion": "Change this",
            }
        ]
        job = enqueue(db, db.get(Project, pid), "resolve_validations", {})
        db.commit()
        return segment.id, job.id, segment.units[0]["id"]


async def context(*args, **kwargs):
    return SimpleNamespace(messages=[], inspector={})


def verdict(issues=None):
    return FinalReviewResult(issues=issues or [], uncertainties=[], explanation="Réexamen terminé.")


async def test_final_review_clears_obsolete_critique_and_keeps_text(seeded, monkeypatch):
    sid, jid, _ = prepare(seeded[0])
    calls = []

    async def answer(**kwargs):
        calls.append(kwargs)
        return verdict()

    monkeypatch.setattr(final_review, "build_context", context)
    monkeypatch.setattr(final_review.llm, "complete", answer)
    await execute(*claim())
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert segment.status == "ok" and not segment.critique
        assert segment.translation == "Le retour" and not segment.validated and not segment.human
        assert db.get(Job, jid).status == "completed"
    assert len(calls) == 1


async def test_final_review_never_calls_model_for_human_text(seeded, monkeypatch):
    sid, _, _ = prepare(seeded[0], human=True)

    async def unexpected(**kwargs):
        pytest.fail("Human choices must not be reviewed automatically")

    monkeypatch.setattr(final_review.llm, "complete", unexpected)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Segment, sid).critique


@pytest.mark.parametrize("verified", [True, False])
async def test_final_review_applies_only_verified_correction(seeded, monkeypatch, verified):
    sid, jid, unit_id = prepare(seeded[0])
    issue = {
        "unit_id": unit_id,
        "category": "meaning",
        "severity": "error",
        "description": "Meaning is wrong",
        "suggestion": "Retour au phare",
    }
    responses = iter([verdict([issue]), verdict([] if verified else [issue])])

    async def answer(**kwargs):
        return next(responses)

    async def revision(*args, **kwargs):
        return TranslationResult(units=[{"id": unit_id, "text": "Retour au phare"}])

    monkeypatch.setattr(final_review, "build_context", context)
    monkeypatch.setattr(final_review.llm, "complete", answer)
    monkeypatch.setattr(pipeline, "translation_call", revision)
    await execute(*claim())
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert segment.translation == ("Retour au phare" if verified else "Le retour")
        assert segment.status == ("ok" if verified else "check")
        assert sid in db.get(Job, jid).checkpoint["final_review_done"]


async def test_final_review_retains_non_recomputed_issues(seeded, monkeypatch):
    sid, _, _ = prepare(seeded[0])
    with SessionLocal() as db:
        db.add(
            Issue(
                project_id=seeded[0],
                segment_id=sid,
                code="consistency",
                severity="warning",
                message="Global issue",
            )
        )
        db.commit()

    async def answer(**kwargs):
        return verdict()

    monkeypatch.setattr(final_review, "build_context", context)
    monkeypatch.setattr(final_review.llm, "complete", answer)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Segment, sid).status == "check"
        assert db.scalar(select(Issue).where(Issue.segment_id == sid)).resolved is False


async def test_final_review_outage_is_resumable(seeded, monkeypatch):
    sid, jid, _ = prepare(seeded[0])

    async def outage(**kwargs):
        raise ProviderUnavailable("offline")

    monkeypatch.setattr(final_review, "build_context", context)
    monkeypatch.setattr(final_review.llm, "complete", outage)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "waiting"
        assert sid not in db.get(Job, jid).checkpoint.get("final_review_done", [])


async def test_human_edit_during_final_review_wins(seeded, monkeypatch):
    from app.engines.translation.versions import save_version

    sid, _, unit_id = prepare(seeded[0])

    async def answer(**kwargs):
        with SessionLocal() as db:
            assert save_version(db, sid, [{"id": unit_id, "text": "Mon choix humain"}], "human", 0)
            db.commit()
        return verdict()

    monkeypatch.setattr(final_review, "build_context", context)
    monkeypatch.setattr(final_review.llm, "complete", answer)
    await execute(*claim())
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert segment.human and segment.translation == "Mon choix humain"
        assert segment.critique


@respx.mock
async def test_searxng_disabled_makes_no_request(monkeypatch):
    monkeypatch.setattr(settings(), "searxng_url", "")
    evidence = await final_review.web_evidence(["some term"])
    assert not evidence["enabled"] and not respx.calls


@respx.mock
async def test_searxng_is_bounded_and_failure_is_not_evidence(monkeypatch):
    monkeypatch.setattr(settings(), "searxng_url", "https://search.test")
    route = respx.get("https://search.test/search").respond(503)
    evidence = await final_review.web_evidence(["term one", "term two", "term three"])
    assert route.call_count == 2 and evidence["unavailable"] and not evidence["sources"]
