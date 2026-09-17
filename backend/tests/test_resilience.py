import asyncio
import time

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_pipeline import mock_completion

from app.db import SessionLocal
from app.jobs.queue import claim, enqueue, suspend
from app.jobs.worker import execute, provider_dispatcher
from app.models import Job, Project, RequestLog, Segment


def prepare(pid, force=False):
    with SessionLocal() as db:
        project = db.get(Project, pid)
        project.bible = {"summary": "Known book context"}
        job = enqueue(db, project, "translate", {"force": force})
        db.commit()
        return job.id


@respx.mock
async def test_service_outage_waits_and_recovers(seeded):
    pid = seeded[0]
    jid = prepare(pid)
    route = respx.post("https://llm.test/v1/chat/completions").respond(503, headers={"Retry-After": "45"})
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "waiting" and job.outage_count == 1
        assert job.next_attempt >= time.time() + 40
        assert job.stop_reason == "provider_unavailable"
        log = db.scalar(select(RequestLog))
        assert log.job_id == jid and log.status == "error"
        assert (
            db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)).status
            == "waiting"
        )
    assert route.call_count == 1 and claim() is None
    with SessionLocal() as db:
        db.get(Job, jid).next_attempt = time.time() - 1
        db.commit()
    route.mock(side_effect=mock_completion)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"
        assert all(s.translation for s in db.scalars(select(Segment).where(Segment.project_id == pid)))


@respx.mock
async def test_authentication_failure_requires_manual_resume(seeded):
    jid = prepare(seeded[0])
    route = respx.post("https://llm.test/v1/chat/completions").respond(401)
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "blocked" and job.next_attempt == 0
        assert job.stop_reason == "authentication_required"
    assert route.call_count == 1 and claim() is None


@respx.mock
async def test_user_pause_cancels_inflight_request_and_stays_paused(seeded):
    jid = prepare(seeded[0])
    entered = asyncio.Event()

    async def never_finishes(_request):
        entered.set()
        await asyncio.Future()

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=never_finishes)
    work = asyncio.create_task(execute(*claim()))
    await asyncio.wait_for(entered.wait(), 5)
    with SessionLocal() as db:
        job = db.get(Job, jid)
        job.status, job.lease_owner, job.stop_reason = "paused", "", "user_pause"
        db.commit()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(work, 5)
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "paused"
        log = db.scalar(select(RequestLog))
        assert log.status == "interrupted"
    assert claim() is None


@respx.mock
async def test_worker_shutdown_requeues_with_checkpoint_and_request_ownership(seeded):
    jid = prepare(seeded[0])
    entered = asyncio.Event()

    async def never_finishes(_request):
        entered.set()
        await asyncio.Future()

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=never_finishes)
    _, owner = claim()
    work = asyncio.create_task(execute(jid, owner))
    await asyncio.wait_for(entered.wait(), 5)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "pending" and job.stop_reason == "worker_interrupted"
        assert job.checkpoint["segment_id"]
        log = db.scalar(select(RequestLog))
        assert log.status == "interrupted" and log.execution_owner == owner
    recovered = claim()
    assert recovered[0] == jid and recovered[1] != owner


def test_late_recovery_cannot_override_user_pause(seeded):
    jid = prepare(seeded[0])
    _, owner = claim()
    with SessionLocal() as db:
        job = db.get(Job, jid)
        job.status, job.lease_owner = "paused", ""
        db.commit()
    suspend(jid, owner, "waiting", "provider_unavailable", "late failure")
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "paused"


def test_database_enforces_one_live_job_per_project(seeded):
    pid = seeded[0]
    prepare(pid)
    with SessionLocal() as db:
        db.add(Job(project_id=pid, operation="translate", status="waiting"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_default_retry_delay_remains_predictable_after_repeated_outages(seeded):
    jid = prepare(seeded[0])
    expected_base_delays = [60, 120, 240, 480, 960, 1920, 3600]  # Exponential backoff
    for count in range(1, 8):
        if count > 1:
            with SessionLocal() as db:
                db.get(Job, jid).next_attempt = 0
                db.commit()
        _, owner = claim()
        start = time.time()
        suspend(jid, owner, "waiting", "provider_unavailable", "offline")
        with SessionLocal() as db:
            job = db.get(Job, jid)
            assert job.outage_count == count
            base = expected_base_delays[count - 1]
            # Allow 0-10% jitter plus 20% margin
            assert base <= job.next_attempt - start < base * 1.3


@respx.mock
async def test_pause_during_content_refusal_does_not_escape_the_job(seeded):
    with SessionLocal() as db:
        jid = enqueue(db, db.get(Project, seeded[0]), "analyze", {}).id
        db.commit()

    def refuse_after_pause(_request):
        with SessionLocal() as db:  # The user pauses while the request is in flight.
            db.get(Job, jid).status = "paused"
            db.commit()
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "content_filter", "message": {"content": ""}}]}
        )

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=refuse_after_pause)
    await execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "paused"


@respx.mock
async def test_non_finite_retry_after_is_ignored(seeded):
    jid = prepare(seeded[0])
    respx.post("https://llm.test/v1/chat/completions").respond(503, headers={"Retry-After": "inf"})
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "waiting" and job.stop_reason == "provider_unavailable"
        assert time.time() < job.next_attempt < time.time() + 3700


async def test_dispatcher_survives_a_job_that_raises(seeded, monkeypatch, caplog):
    first = prepare(seeded[0])
    started = []

    async def explode(job_id, _owner):
        started.append(job_id)
        raise RuntimeError("secret book sentence that must stay out of the logs")

    monkeypatch.setattr("app.jobs.worker.execute", explode)
    stopped = asyncio.Event()
    dispatcher = asyncio.create_task(provider_dispatcher(stopped))
    for _ in range(100):
        if started:
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    assert started == [first] and not dispatcher.done()
    stopped.set()
    await asyncio.wait_for(dispatcher, timeout=5)
    assert "status=task_failed" in caplog.text and "RuntimeError" in caplog.text
    assert "secret book sentence" not in caplog.text
