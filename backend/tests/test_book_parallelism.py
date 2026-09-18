"""Several passages of one book in flight at once, and a worker loop that big books cannot stall."""

import asyncio
import json
import re
import time

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from app.api.projects import control, jobs, project_view
from app.config import settings
from app.db import SessionLocal
from app.engines.translation import pipeline
from app.jobs import segment_state as state
from app.jobs import worker
from app.jobs.concurrency import book_parallelism, in_parallel
from app.jobs.queue import claim, enqueue
from app.models import Chapter, Event, Job, Project, Provider, RequestLog, Segment, TranslationVersion, User
from app.schemas import TranslationResult

URL = "https://llm.test/v1/chat/completions"


def book(passages: int, capacity: int = 4, title: str = "Synthetic") -> tuple[str, str]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "parallel"))
        if user is None:
            user = User(username="parallel", password_hash="unused", admin=True)
            db.add(user)
        provider = db.scalar(select(Provider).where(Provider.name == "Parallel"))
        if provider is None:
            provider = Provider(
                name="Parallel",
                base_url="https://llm.test/v1",
                model="test-model",
                capabilities={"supports_json_schema": True},
                context_window=64000,
                max_concurrency=capacity,
            )
            db.add(provider)
        db.flush()
        project = Project(
            owner_id=user.id,
            title=title,
            original_hash="0" * 64,
            original_path="/dev/null",
            provider_id=provider.id,
            quality="fast",
            context_backend="internal",
            bible={"summary": "Synthetic book"},
        )
        db.add(project)
        db.flush()
        chapter = Chapter(project_id=project.id, position=0, title="One", resource="one.xhtml")
        db.add(chapter)
        db.flush()
        db.add_all(
            Segment(
                project_id=project.id,
                chapter_id=chapter.id,
                position=position,
                source=f"Alice opened door number {position}.",
                units=[{"id": f"p{position}", "text": f"Alice opened door number {position}."}],
            )
            for position in range(passages)
        )
        db.commit()
        return project.id, user.id


def start(project_id: str, operation: str = "translate", **options) -> str:
    with SessionLocal() as db:
        job = enqueue(db, db.get(Project, project_id), operation, options)
        db.commit()
        return job.id


def answer(body: dict) -> httpx.Response:
    name = body.get("response_format", {}).get("json_schema", {}).get("name", "TranslationResult")
    if name == "FinalReviewResult":
        result = {"decision": "accept", "issues": [], "uncertainties": [], "explanation": "ok", "search_queries": []}
    else:
        text = "\n".join(m["content"] for m in body["messages"])
        target = json.loads(re.search(r"<TARGET_TEXT>\n(.*?)\n</TARGET_TEXT>", text, re.S)[1])
        result = {"units": [{"id": u["id"], "text": f"Alice ouvrit la porte {u['id']}."} for u in target]}
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}]})


class Provider_:
    """A fake provider that counts the calls in flight and can hold them."""

    def __init__(self, latency: float = 0.0):
        self.latency, self.flight, self.peak, self.calls = latency, 0, 0, 0
        self.held = asyncio.Event()
        self.held.set()
        self.hold_after = None

    async def __call__(self, request):
        self.calls += 1
        self.flight += 1
        self.peak = max(self.peak, self.flight)
        try:
            if self.hold_after is not None and self.calls > self.hold_after:
                await self.held.wait()
            await asyncio.sleep(self.latency)
            return answer(json.loads(request.content))
        finally:
            self.flight -= 1


def translated_once(project_id: str) -> None:
    with SessionLocal() as db:
        segments = db.scalars(select(Segment).where(Segment.project_id == project_id)).all()
        assert all(s.translation and s.stage == "done" for s in segments)
        applied = db.execute(
            select(TranslationVersion.segment_id, func.count())
            .join(Segment, Segment.id == TranslationVersion.segment_id)
            .where(Segment.project_id == project_id, TranslationVersion.applied.is_(True))
            .group_by(TranslationVersion.segment_id)
        ).all()
        assert len(applied) == len(segments) and all(count == 1 for _, count in applied)


def step_events(project_id: str, job_id: str, step: str) -> int:
    with SessionLocal() as db:
        payloads = db.scalars(select(Event.payload).where(Event.project_id == project_id)).all()
    return sum(1 for p in payloads if p.get("job_id") == job_id and p.get("step") == step)


async def run(project_id: str, operation: str = "translate", **options) -> Job:
    jid = start(project_id, operation, **options)
    claimed = claim()
    assert claimed and claimed[0] == jid
    await worker.execute(*claimed)
    with SessionLocal() as db:
        return db.get(Job, jid)


@respx.mock
async def test_passages_of_a_book_run_side_by_side_up_to_the_provider_capacity(monkeypatch):
    elapsed, peaks = {}, {}
    for parallelism in (1, 0):
        monkeypatch.setattr(settings(), "worker_book_parallelism", parallelism)
        fake = Provider_(latency=0.1)
        respx.post(URL).mock(side_effect=fake)
        pid, _ = book(16, title=f"Book {parallelism}")
        began = time.monotonic()
        job = await run(pid, full_review=True)
        elapsed[parallelism], peaks[parallelism] = time.monotonic() - began, fake.peak
        assert job.status == "completed", job.error
        assert fake.calls == 32  # one translation and one final review per passage, none repeated
        translated_once(pid)
        with SessionLocal() as db:
            assert len(state.marked(db, job.id, state.REVIEWED)) == 16
    assert peaks == {1: 1, 0: 4}
    # The peaks prove the passages overlap; the speed-up only has to show it pays off. A loaded CI
    # runner measured 2.4 where a workstation measures 3.6, so the bound leaves room for that.
    assert elapsed[1] / elapsed[0] > 1.8


@respx.mock
async def test_an_interrupted_parallel_batch_resumes_without_loss_or_duplicate(monkeypatch):
    monkeypatch.setattr(settings(), "final_review_enabled", False)
    fake = Provider_()
    fake.hold_after = 6  # six passages done, the next four stay in flight
    fake.held.clear()
    respx.post(URL).mock(side_effect=fake)
    pid, _ = book(12)
    jid = start(pid)
    task = asyncio.create_task(worker.execute(*claim()))
    for _ in range(200):
        if fake.flight == 4 and fake.calls == 10:
            break
        await asyncio.sleep(0.02)
    assert fake.flight == 4
    task.cancel()  # what a worker shutdown does to its jobs
    with pytest.raises(asyncio.CancelledError):
        await task
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "pending" and job.stop_reason == "worker_interrupted"
        assert len(state.marked(db, jid, state.FINISHED)) == 6
        statuses = db.scalars(select(RequestLog.status).where(RequestLog.job_id == jid)).all()
        assert sorted(statuses).count("interrupted") == 4
    fake.held.set()
    claimed = claim()
    assert claimed[0] == jid
    await worker.execute(*claimed)
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"
    translated_once(pid)
    assert fake.calls == 10 + 6  # only the four interrupted passages and the two never started
    assert step_events(pid, jid, "translation") == 10 + 6  # nothing announced again for settled passages


@respx.mock
async def test_a_pause_interrupts_every_call_in_flight(monkeypatch):
    monkeypatch.setattr(settings(), "worker_heartbeat_seconds", 1)
    monkeypatch.setattr(settings(), "final_review_enabled", False)
    fake = Provider_()
    fake.hold_after = 0
    fake.held.clear()
    respx.post(URL).mock(side_effect=fake)
    pid, user_id = book(8)
    jid = start(pid)
    task = asyncio.create_task(worker.execute(*claim()))
    for _ in range(200):
        if fake.flight == 4:
            break
        await asyncio.sleep(0.02)
    assert fake.flight == 4
    with SessionLocal() as db:
        control(pid, jid, "pause", db.get(User, user_id), db)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)  # the heartbeat sees the pause and cancels the job
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "paused"
        statuses = db.scalars(select(RequestLog.status).where(RequestLog.job_id == jid)).all()
        assert statuses and all(status == "interrupted" for status in statuses)
        assert not db.scalar(select(Segment.id).where(Segment.project_id == pid, Segment.translation != ""))
        assert not state.marked(db, jid, state.FINISHED)
    assert fake.flight == 0
    fake.held.set()
    with SessionLocal() as db:
        control(pid, jid, "resume", db.get(User, user_id), db)
    await worker.execute(*claim())
    with SessionLocal() as db:
        assert db.get(Job, jid).status == "completed"
    translated_once(pid)


async def test_checkpoint_and_job_listing_stay_small_on_a_5000_passage_book(monkeypatch):
    pid, user_id = book(5000)
    jid = start(pid, full_review=True)
    # A resumed job that had settled all but the last passages: its state is 5,000 rows, not a list.
    with SessionLocal() as db:
        ids = db.scalars(select(Segment.id).where(Segment.project_id == pid).order_by(Segment.position)).all()
        for segment in db.scalars(select(Segment).where(Segment.project_id == pid, Segment.position < 4990)):
            segment.translation = "Traduit"
            segment.translated_units = [{"id": u["id"], "text": "Traduit"} for u in segment.units]
            segment.stage, segment.status = "done", "ok"
        state.mark_all(db, jid, state.FINISHED, ids[:4990])
        state.mark_all(db, jid, state.REVIEW_TARGET, ids)
        for sid in ids[:4990]:
            state.mark(db, jid, state.REVIEWED, sid, outcome="resolved", data={"revised": False})
        db.get(Job, jid).checkpoint = {"step": "final_review", "current": 4990, "total": 5000, "review_targets": 5000}
        db.commit()

    async def translation(project, segment, *args, **kwargs):
        return TranslationResult(units=[{"id": u["id"], "text": "Traduction"} for u in segment.units])

    monkeypatch.setattr(pipeline, "translation_call", translation)
    with respx.mock:
        respx.post(URL).mock(side_effect=Provider_())
        claimed = claim()
        await worker.execute(*claimed)
    with SessionLocal() as db:
        job = db.get(Job, jid)
        assert job.status == "completed", job.error
        assert len(json.dumps(job.checkpoint)) < 4096
        assert len(state.marked(db, jid, state.REVIEWED)) == 5000
        listing = jobs(pid, db.get(User, user_id), db)
        assert len(json.dumps(listing)) < 4096
        began = time.monotonic()
        progress = project_view(db, db.get(Project, pid))["progress"]
        assert time.monotonic() - began < 6  # 5 000 passages; well under a second on a workstation
        assert progress["review"]["examined"] == 5000
    assert step_events(pid, jid, "translation") == 10
    assert step_events(pid, jid, "final_review") == 10


async def test_two_big_books_keep_their_lease_while_sharing_the_worker(monkeypatch):
    monkeypatch.setattr(settings(), "worker_heartbeat_seconds", 1)
    monkeypatch.setattr(settings(), "final_review_enabled", False)
    books = [book(1000, title=f"Big {n}")[0] for n in range(2)]
    with SessionLocal() as db:
        for pid in books:
            enqueue(db, db.get(Project, pid), "translate", {})
        db.commit()
    renewals: dict[str, list[float]] = {}
    checkpoint = worker.checkpoint

    def renew(job_id, owner, progress=None):
        job = checkpoint(job_id, owner, progress)
        renewals.setdefault(job_id, []).append(time.monotonic())
        return job

    async def translation(project, segment, *args, **kwargs):
        await asyncio.sleep(0)
        return TranslationResult(units=[{"id": u["id"], "text": "Traduction"} for u in segment.units])

    monkeypatch.setattr(worker, "checkpoint", renew)
    monkeypatch.setattr(pipeline, "translation_call", translation)
    lags, done = [], asyncio.Event()

    async def watch():
        while not done.is_set():
            before = time.monotonic()
            await asyncio.sleep(0.05)
            lags.append(time.monotonic() - before - 0.05)

    watcher = asyncio.create_task(watch())
    claimed = [claim(), claim()]
    began = time.monotonic()
    await asyncio.gather(*(worker.execute(*item) for item in claimed))
    finished = time.monotonic()
    done.set()
    await watcher
    with SessionLocal() as db:
        for job in db.scalars(select(Job).where(Job.project_id.in_(books))):
            assert job.status == "completed" and job.attempts == 1  # never reclaimed
            assert len(json.dumps(job.checkpoint)) < 4096
    for job_id, times in renewals.items():
        # A heartbeat per second from start to finish. The bound is what matters for the 60 s lease
        # (well under the heartbeat's 40 s grace), with room for a loaded CI runner: the old event
        # loop stalls made gaps of tens of seconds, not a few.
        marks = [began, *times, finished]
        assert max(b - a for a, b in zip(marks, marks[1:], strict=False)) < 10, job_id
    assert max(lags) < 2


def test_book_parallelism_shares_the_provider_and_honours_the_setting(seeded, monkeypatch):
    pid, _, provider_id = seeded
    with SessionLocal() as db:
        db.get(Provider, provider_id).max_concurrency = 4
        db.commit()
    assert book_parallelism(provider_id) == 4
    monkeypatch.setattr(settings(), "worker_book_parallelism", 1)
    assert book_parallelism(provider_id) == 1  # the former strictly sequential behaviour
    monkeypatch.setattr(settings(), "worker_book_parallelism", 8)
    assert book_parallelism(provider_id) == 4  # never beyond the provider's capacity
    monkeypatch.setattr(settings(), "worker_book_parallelism", 0)
    start(pid)
    other = book(1, title="Other")[0]
    with SessionLocal() as db:
        db.get(Project, other).provider_id = provider_id
        db.commit()
    start(other)
    assert claim() and claim()
    assert book_parallelism(provider_id) == 2  # two books running: half each


class Boom(Exception):
    pass


async def test_the_first_failure_stops_the_other_passages():
    started, cancelled = [], []

    async def work(number):
        started.append(number)
        try:
            if number == 2:
                raise Boom()
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(number)
            raise

    async def launch(number):
        return work(number)

    async def width():
        return 3

    with pytest.raises(Boom):
        await asyncio.wait_for(in_parallel(range(10), width, launch), 5)
    assert started == [0, 1, 2]
    assert sorted(cancelled) == [0, 1]


@pytest.mark.parametrize("action", ["edit", "retain_source"])
def test_a_human_action_during_a_worker_write_cannot_deadlock(seeded, action):
    """The worker holds its job row while it writes a passage; the API must take the job row first too."""
    import threading

    from fastapi import HTTPException

    from app.api.coverage import RevisionInput, retain_source
    from app.api.segments import edit
    from app.db import engine
    from app.engines.translation.versions import save_version
    from app.jobs.queue import fence
    from app.schemas import EditInput

    WAITING = text(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    if engine.dialect.name != "postgresql":
        pytest.skip("row locks and deadlock detection need PostgreSQL")
    pid, user_id, _ = seeded
    jid = start(pid)
    _, owner = claim()
    with SessionLocal() as db:
        segment = db.scalar(select(Segment).where(Segment.project_id == pid).order_by(Segment.position))
        sid, units = segment.id, [{"id": u["id"], "text": u["text"]} for u in segment.units]
    job_locked, outcome = threading.Event(), {}

    def worker_write():
        try:
            with SessionLocal() as db:
                fence(db, jid, owner)  # job row locked, as in persist()
                job_locked.set()
                deadline = time.monotonic() + 5
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as probe:
                    while time.monotonic() < deadline and not probe.scalar(WAITING):
                        time.sleep(0.05)
                assert time.monotonic() < deadline, "the API request never waited for the job row"
                save_version(db, sid, [{**u, "text": "Traduit"} for u in units], "translation", 0)
                db.commit()
            outcome["worker"] = "ok"
        except Exception as exc:  # noqa: BLE001 - the assertion below reports it
            outcome["worker"] = repr(exc)

    def human_write():
        job_locked.wait(5)
        try:
            with SessionLocal() as db:
                user = db.get(User, user_id)
                if action == "edit":
                    edit(sid, EditInput(revision=0, units=[{**u, "text": "Humain"} for u in units]), user, db)
                else:
                    retain_source(sid, RevisionInput(revision=0), user, db)
            outcome["api"] = "ok"
        except HTTPException as exc:
            outcome["api"] = exc.status_code
        except Exception as exc:  # noqa: BLE001
            outcome["api"] = repr(exc)

    threads = [threading.Thread(target=worker_write), threading.Thread(target=human_write)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    # The API waited for the worker's transaction, then saw the new revision: a clean conflict.
    assert outcome == {"worker": "ok", "api": 409}
