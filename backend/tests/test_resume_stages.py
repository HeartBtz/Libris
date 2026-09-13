import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
import respx
from fastapi import HTTPException
from sqlalchemy import select
from test_pipeline import mock_completion
from test_resilience import prepare

from app.db import SessionLocal
from app.jobs.queue import claim
from app.jobs.worker import execute
from app.models import Job, Project, Segment


@respx.mock
async def test_forced_retry_resumes_review_without_overwriting_saved_translation(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        db.get(Project, pid).quality = "normal"
        db.commit()
    jid = prepare(pid, force=True)
    fail_review = True

    def answer(request):
        nonlocal fail_review
        payload = json.loads(request.content)
        if (
            fail_review
            and payload.get("response_format", {}).get("json_schema", {}).get("name") == "ReviewResult"
        ):
            fail_review = False
            return httpx.Response(503)
        return mock_completion(request)

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=answer)
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "waiting"
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        assert segment.stage == "translated" and segment.revision == 1
        sid, translation = segment.id, segment.translation
        job.next_attempt = 0
        db.commit()
    await execute(*claim())
    with SessionLocal() as db:
        segment = db.get(Segment, sid)
        assert segment.revision == 1 and segment.translation == translation
        assert db.get(Job, jid).status == "completed"


async def test_codex_interrupt_only_targets_exact_request(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from codex_bridge import app as bridge

    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def generation(_body):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def session_for(_pid):
        return SimpleNamespace(complete=generation)

    async def still_connected():
        return False

    monkeypatch.setattr(bridge, "session_for", session_for)
    pid = UUID("00000000-0000-4000-8000-000000000444")
    request_id = UUID("00000000-0000-4000-8000-000000000555")
    body = bridge.Completion(
        model="test-model",
        messages=[],
        schema={},
        timeout=60,
        context_window=32768,
        output_reservation=4096,
        request_id=request_id,
    )
    work = asyncio.create_task(bridge.complete(pid, body, SimpleNamespace(is_disconnected=still_connected)))
    await entered.wait()
    wrong = await bridge.interrupt(
        UUID("00000000-0000-4000-8000-000000000666"), bridge.Interrupt(request_id=request_id)
    )
    assert not wrong["interruption_requested"] and not cancelled.is_set()
    right = await bridge.interrupt(pid, bridge.Interrupt(request_id=request_id))
    assert right["interruption_requested"]
    with pytest.raises(HTTPException) as error:
        await work
    assert error.value.status_code == 409 and cancelled.is_set()
    assert not bridge.running
