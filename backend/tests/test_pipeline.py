import json
import re
import time

import httpx
import pytest
import respx
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.translation.versions import save_version
from app.jobs.queue import JobStopped, claim, enqueue, fence
from app.jobs.worker import execute
from app.models import Job, Project, Provider, RequestLog, Segment, TranslationVersion
from app.providers.llm import llm
from app.schemas import BookBible, BookOverview, ChapterAnalysis, ReviewResult, TranslationResult


def mock_completion(request):
    body = json.loads(request.content)
    schema_name = body.get("response_format", {}).get("json_schema", {}).get("name", "TranslationResult")
    if schema_name == "ChapterAnalysis":
        result = ChapterAnalysis(summary="Alice and Bob discuss the pendant.").model_dump()
    elif schema_name in {"BookBible", "BookOverview"}:
        result = (BookOverview if schema_name == "BookOverview" else BookBible)(
            title="The Silver Tower", summary="A mystery about a pendant.", tone="Literary"
        ).model_dump()
    elif schema_name == "ReviewResult":
        result = ReviewResult().model_dump()
    else:
        text = "\n".join(m["content"] for m in body["messages"])
        target = json.loads(re.search(r"<TARGET_TEXT>\n(.*?)\n</TARGET_TEXT>", text, re.S)[1])
        units = [
            {
                "id": u["id"],
                "text": u["text"]
                .replace("Chapter", "Chapitre")
                .replace("Silver Tower", "Tour d’argent")
                .replace("Alice entered the", "Alice entra dans la")
                .replace("and stopped.", "et s’arrêta."),
            }
            for u in target
        ]
        result = TranslationResult(units=units).model_dump()
    return httpx.Response(
        200,
        json={
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80},
        },
    )


async def run_job(pid, operation, **options):
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, pid), operation, options)
        db.commit()
        jid = job.id
    claimed = claim()
    assert claimed and claimed[0] == jid
    await execute(*claimed)
    with SessionLocal() as db:
        return db.get(Job, jid)


@respx.mock
async def test_full_analysis_translation_cache_and_export(seeded):
    pid, _, _ = seeded
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    analysis = await run_job(pid, "analyze")
    assert analysis.status == "completed", analysis.error
    with SessionLocal() as db:
        assert db.get(Project, pid).bible["summary"]
    translated = await run_job(pid, "translate")
    assert translated.status == "completed", translated.error
    with SessionLocal() as db:
        segments = list(db.scalars(select(Segment).where(Segment.project_id == pid)))
        assert all(s.translation for s in segments)
        assert all(s.stage == "done" for s in segments)
        logs = list(db.scalars(select(RequestLog).where(RequestLog.project_id == pid)))
        assert all(r.status == "success" for r in logs)
        assert any("PREVIOUS_CONTEXT" in str(r.messages) for r in logs)
    previous_calls = route.call_count
    again = await run_job(pid, "translate")
    assert again.status == "completed"
    assert route.call_count == previous_calls


def test_human_edit_wins_over_inflight_ai(seeded):
    pid, user_id, _ = seeded
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid))
        sid = segment.id
        human = [{"id": u["id"], "text": u["text"] + " humain"} for u in segment.units]
        model = [{"id": u["id"], "text": u["text"] + " automatique"} for u in segment.units]
        assert save_version(db, sid, human, "human", 0, author_id=user_id, validated=True)
        db.commit()
    with SessionLocal() as db:
        assert not save_version(db, sid, model, "translation", 0)
        db.commit()
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert segment.translated_units == human
        assert segment.validated
        versions = list(db.scalars(select(TranslationVersion).where(TranslationVersion.segment_id == sid)))
        assert len(versions) == 2
        assert sum(v.applied for v in versions) == 1
        assert not save_version(db, sid, model, "translation", segment.revision)


def test_expired_lease_reclaimed_and_old_worker_fenced(seeded):
    pid, _, _ = seeded
    with SessionLocal() as db:
        enqueue(db, db.get(Project, pid), "analyze", {})
        db.commit()
    jid, owner = claim()
    with SessionLocal() as db:
        db.get(Job, jid).lease_until = time.time() - 1
        db.commit()
    new_id, new_owner = claim()
    assert new_id == jid and new_owner != owner
    with SessionLocal() as db:
        with pytest.raises(JobStopped):
            fence(db, jid, owner)
        assert fence(db, jid, new_owner)


@respx.mock
async def test_pause_retains_completed_segments_and_resume(seeded):
    pid, _, _ = seeded
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.bible = {"summary": "Test"}
        first = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        first.translation = "human work"
        first.stage = "done"
        first.human = True
        db.commit()
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    completed = await run_job(pid, "translate")
    assert completed.status == "completed", completed.error
    with SessionLocal() as db:
        assert db.get(Segment, first.id).translation == "human work"


@respx.mock
async def test_identical_request_is_cached(seeded):
    pid, _, provider = seeded
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    args = dict(
        project_id=pid,
        provider_id=provider,
        operation="book_analysis",
        messages=[{"role": "system", "content": "Analyze the book"}],
        response_model=BookBible,
    )
    a, b = await llm.complete(**args), await llm.complete(**args)
    assert a == b
    assert route.call_count == 1


@respx.mock
async def test_schema_fallback_only_for_capability_error(seeded, monkeypatch):
    pid, _, provider = seeded
    calls = []

    async def no_delay(_):
        pass

    monkeypatch.setattr("app.providers.llm.asyncio.sleep", no_delay)

    def response(request):
        body = json.loads(request.content)
        calls.append(body)
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": "response_format json_schema unsupported"})
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"summary":"fine"}'}}]}
        )

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=response)
    result = await llm.complete(
        project_id=pid,
        provider_id=provider,
        operation="book_analysis",
        messages=[{"role": "system", "content": "Analyze"}],
        response_model=BookBible,
    )
    assert result.summary == "fine"
    assert len(calls) == 2 and calls[-1]["response_format"]["type"] == "json_object"


@respx.mock
async def test_refused_only_job_uses_override_provider_without_touching_other_passages(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        target = db.scalar(
            select(Segment).where(Segment.project_id == pid, Segment.source.contains("Chapter"))
        )
        target.status = "refused"
        alternate = Provider(
            name="Uncensored recovery",
            base_url="https://uncensored.test/v1",
            model="recovery-model",
            capabilities={"supports_json_schema": True},
            context_window=64000,
        )
        db.add(alternate)
        db.commit()
        alternate_id, target_id = alternate.id, target.id
    route = respx.post("https://uncensored.test/v1/chat/completions").mock(side_effect=mock_completion)

    job = await run_job(
        pid,
        "translate",
        refused_only=True,
        provider_id=alternate_id,
        force=True,
    )

    with SessionLocal() as db:
        translated = list(db.scalars(select(Segment).where(Segment.project_id == pid, Segment.translation != "")))
        request = db.scalar(select(RequestLog).where(RequestLog.segment_id == target_id))
        assert job.status == "completed"
        assert [segment.id for segment in translated] == [target_id]
        assert request.provider_id == alternate_id
    assert route.call_count == 1
