import json

import pytest
import respx
from sqlalchemy import select

from app.db import SessionLocal
from app.engines.translation import pipeline
from app.jobs.queue import claim, enqueue
from app.jobs.worker import execute
from app.models import Job, Project, Segment
from app.providers.llm import InvalidResponseExhausted
from app.schemas import TranslationResult


@pytest.mark.parametrize("success_at", [None, 5])
async def test_stop_after_ten_failed_passages_and_reset_on_success(seeded, monkeypatch, success_at):
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.quality = "fast"
        existing = list(db.scalars(select(Segment).where(Segment.project_id == pid)))
        for item in existing:
            item.stage = "done"
        offset = max(s.position for s in existing) + 1
        for index in range(12):
            db.add(Segment(project_id=pid, chapter_id=existing[0].chapter_id,
                           position=offset + index, source="Hello", units=[{"id": "u1", "text": "Hello"}]))
        job = enqueue(db, project, "translate", {})
        db.commit()
        jid = job.id
    calls = []

    async def reply(project, segment, *args, **kwargs):
        index = segment.position - offset
        calls.append(index)
        if index == success_at:
            return TranslationResult(units=[{"id": "u1", "text": "Bonjour"}])
        raise InvalidResponseExhausted("Five invalid responses")

    monkeypatch.setattr(pipeline, "translation_call", reply)
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == ("failed" if success_at is None else "completed")
        assert job.checkpoint["consecutive_failures"] == (10 if success_at is None else 6)
        assert len(calls) == (10 if success_at is None else 12)
        assert len(job.checkpoint["finished_ids"]) == len(calls)
        if success_at is None:
            assert job.stop_reason == "consecutive_failures"


@respx.mock
async def test_repeated_invalid_responses_skip_one_passage(seeded, monkeypatch):
    import asyncio

    original_sleep = asyncio.sleep

    async def no_delay(*args):
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_delay)
    pid = seeded[0]
    with SessionLocal() as db:
        project = db.get(Project, pid)
        segment = db.scalar(select(Segment).where(Segment.project_id == pid))
        job = enqueue(db, project, "translate", {"segment_id": segment.id})
        db.commit()
        sid, jid = segment.id, job.id
    route = respx.post("https://llm.test/v1/chat/completions").respond(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": '{"units": []}'}}]})
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Segment, sid).status == "error"
        assert db.get(Job, jid).status == "completed"
        assert db.get(Job, jid).checkpoint["consecutive_failures"] == 1
    # Validation failures stop after three full-price attempts, each one told what was wrong.
    assert route.call_count == 3
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert not any("rejected by validation" in m["content"] for m in bodies[0]["messages"])
    for body in bodies[1:]:
        feedback = [m["content"] for m in body["messages"] if "rejected by validation" in m["content"]]
        assert len(feedback) == 1 and "Paragraphes manquants" in feedback[0]
