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
from app.providers.reliability import calculate_retry_delay


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


def test_retry_delay_backoff_is_a_floor_and_the_cap_holds():
    # A sub-second or tiny Retry-After must never produce an immediate retry or disable the backoff.
    assert calculate_retry_delay(1, 60, 3600, 0.5) == 60
    assert calculate_retry_delay(50, 60, 3600, 1) >= 3600
    # A longer provider request is honoured, rounded up and bounded to 24 hours.
    assert calculate_retry_delay(1, 60, 3600, 90.2) == 91
    assert calculate_retry_delay(1, 60, 3600, 7200) == 7200
    assert calculate_retry_delay(1, 60, 3600, 10**9) == 86400
    assert calculate_retry_delay(3, 60, 3600, float("nan")) >= 240
    # Jitter never exceeds the configured maximum; the first retry stays exact.
    assert max(calculate_retry_delay(9, 60, 3600) for _ in range(2000)) == 3600
    assert {calculate_retry_delay(1, 5, 3600) for _ in range(50)} == {5}
    assert all(240 <= calculate_retry_delay(3, 60, 3600) <= 264 for _ in range(200))


@pytest.mark.parametrize("status", [529, 520, 524, 425])
@respx.mock
async def test_overloaded_and_edge_proxy_statuses_wait_instead_of_failing(seeded, status):
    jid = prepare(seeded[0])
    respx.post("https://llm.test/v1/chat/completions").respond(status, headers={"Retry-After": "120"})
    await execute(*claim())
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "waiting" and job.stop_reason == "provider_unavailable"
        assert job.next_attempt >= time.time() + 115


@respx.mock
async def test_fractional_retry_after_cannot_create_a_hot_retry_loop(seeded):
    prepare(seeded[0])
    route = respx.post("https://llm.test/v1/chat/completions").respond(429, headers={"Retry-After": "0.5"})
    await execute(*claim())
    assert route.call_count == 1 and claim() is None


@respx.mock
async def test_outage_count_only_counts_consecutive_failures(seeded):
    with SessionLocal() as db:
        jid = enqueue(db, db.get(Project, seeded[0]), "analyze", {}).id
        db.commit()
    calls = {"count": 0}

    def flaky(request):
        calls["count"] += 1
        return httpx.Response(503) if calls["count"] in (1, 4) else mock_completion(request)

    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=flaky)
    for _ in range(2):
        with SessionLocal() as db:
            db.get(Job, jid).next_attempt = 0
            db.commit()
        await execute(*claim())
        with SessionLocal() as db:
            job = db.get(Job, jid)
            # Successful calls happened between the two outages: each one is a first failure.
            assert job.status == "waiting" and job.outage_count == 1
            assert job.next_attempt <= time.time() + 61



async def test_worker_intervals_follow_the_configuration(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings, settings
    from app.jobs import worker

    monkeypatch.setattr(settings(), "worker_heartbeat_seconds", 7)
    monkeypatch.setattr(settings(), "memory_catalog_interval_seconds", 3600)
    sleeps = []

    async def record(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    with monkeypatch.context() as patch:
        patch.setattr(worker.asyncio, "sleep", record)
        with pytest.raises(asyncio.CancelledError):
            await worker.heartbeat("job", "owner", asyncio.current_task())
    assert sleeps == [7]
    assert not worker.catalog_due(1000.0, 1120.0) and worker.catalog_due(1000.0, 4601.0)
    # The heartbeat renews a 60 s lease: a value that could let it expire is refused at start-up.
    with pytest.raises(ValidationError):
        Settings(worker_heartbeat_seconds=45)


@respx.mock
async def test_forced_retranslation_asks_the_provider_again(seeded):
    pid = seeded[0]
    route = respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)

    async def run(force):
        jid = prepare(pid, force=force)
        await execute(*claim())
        with SessionLocal() as db:
            assert db.get(Job, jid).status == "completed"
        return route.call_count

    first = await run(False)
    assert first > 0
    with SessionLocal() as db:
        for segment in db.scalars(select(Segment).where(Segment.project_id == pid)):
            segment.translation, segment.translated_units, segment.stage = "", [], "pending"
        db.commit()
    # Same context, no force: every translation is served from the cache.
    assert await run(False) == first
    with SessionLocal() as db:
        assert db.scalar(select(RequestLog).where(RequestLog.cached.is_(True)).limit(1)) is not None
    # Forced: the model is asked again for each passage, and the fresh answers are logged as real calls.
    forced = await run(True)
    with SessionLocal() as db:
        passages = len(list(db.scalars(select(Segment.id).where(Segment.project_id == pid))))
        fresh = list(db.scalars(select(RequestLog).where(RequestLog.cached.is_(False), RequestLog.status == "success")))
    assert forced - first >= passages  # at least one real provider call per passage
    assert len(fresh) == forced
