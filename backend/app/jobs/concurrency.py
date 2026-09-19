"""How the worker runs blocking work and several passages of one book without stalling its event loop.

Every job of a worker shares one event loop: a synchronous query or a long CPU loop there delays the
heartbeats of all the other jobs, which then lose their lease. Database sessions and CPU-bound work
run in threads, each with its own session.
"""

import asyncio
import contextvars
import functools
import threading
import time
import weakref
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.jobs.clock import database_now
from app.models import Job, Project, Provider

T = TypeVar("T")
Item = TypeVar("Item")

# Below the connection pool (5 + 10 overflow): a thread never waits for a free connection.
_database = ThreadPoolExecutor(max_workers=8, thread_name_prefix="libris-db")
# Lease renewals never queue behind bulk work.
_leases = ThreadPoolExecutor(max_workers=2, thread_name_prefix="libris-lease")


def _submit(executor: ThreadPoolExecutor, function: Callable[..., T], *args, **kwargs) -> Awaitable[T]:
    # The job's execution scope (a context variable) must follow the work into the thread.
    call = functools.partial(contextvars.copy_context().run, function, *args, **kwargs)
    return asyncio.get_running_loop().run_in_executor(executor, call)


async def blocking(function: Callable[..., T], *args, **kwargs) -> T:
    return await _submit(_database, function, *args, **kwargs)


async def renewal(function: Callable[..., T], *args, **kwargs) -> T:
    return await _submit(_leases, function, *args, **kwargs)


_job_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_registry = threading.Lock()


def job_lock(job_id: str) -> threading.Lock:
    """Serializes the read-modify-write of one job's checkpoint between the threads of this worker.

    PostgreSQL already does so with `FOR UPDATE`; SQLite reads before taking its write lock, so two
    passages finishing together would otherwise overwrite each other's counters.
    """
    with _registry:
        lock = _job_locks.get(job_id)
        if lock is None:
            lock = _job_locks[job_id] = threading.Lock()
        return lock


def book_parallelism(provider_id: str | None) -> int:
    """Passages of one book in flight at once.

    The provider's capacity is shared between the books running on it (rounded up: the admission queue
    of `llm.complete` keeps the total within capacity), and WORKER_BOOK_PARALLELISM caps the result.
    """
    from app.jobs.queue import RUNNING

    with SessionLocal() as db:
        capacity = db.scalar(select(Provider.max_concurrency).where(Provider.id == provider_id)) or 1
        books = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.provider_id == provider_id, Job.status.in_(RUNNING), Job.lease_until >= database_now())
        )
    share = -(-capacity // max(books, 1))
    wanted = settings().worker_book_parallelism
    return max(1, min(wanted, share) if wanted else share)


def book_share(provider_id: str | None) -> Callable[[], Awaitable[int]]:
    return functools.partial(blocking, book_parallelism, provider_id)


# Threads of a job: one knob for its analysis and its translation (`threads` of the launch, else of the
# volume). It only lowers the book's share of the provider: a big book never takes another book's part.
MAX_THREADS = 64
# After a provider outage (HTTP 429, overload), a resumed job runs at half its width, then regains one
# passage in flight per THROTTLE_RAMP_SECONDS without a new outage.
THROTTLE_RAMP_SECONDS = 60
_last_width: dict[str, int] = {}


def threads_value(chosen) -> int | None:
    try:
        value = int(chosen) if chosen is not None else 0
    except (TypeError, ValueError):
        return None
    return min(value, MAX_THREADS) if value > 0 else None


def job_threads(db, job: Job) -> int | None:
    """The launch's `threads`, else the volume's `config.threads`; None follows the provider's share."""
    chosen = (job.options or {}).get("threads")
    if chosen is None:
        project = db.get(Project, job.project_id)
        chosen = (project.config or {}).get("threads") if project else None
    return threads_value(chosen)


def budget_width(job_id: str) -> int | None:
    """How many calls a job's budget allows in flight at once; None: no budget near its cap.

    The hook where a cost cap reserves the calls a job launches side by side before they start: near
    the cap, the calls already in flight count as spent, and the job narrows (down to one call) so
    that parallel calls cannot overshoot the cap together.
    """
    return None


def job_parallelism(job_id: str, owner: str, provider_id: str | None) -> int:
    """The width of one job: the provider's share, its threads, an outage throttle and its budget."""
    width = book_parallelism(provider_id)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        threads = job_threads(db, job) if job else None
        if threads:
            width = max(1, min(width, threads))
        progress = dict(job.checkpoint or {}) if job else {}
        throttle = progress.get("throttle")
        if throttle:
            since = float(progress.get("throttle_at") or 0)
            if time.time() - since >= THROTTLE_RAMP_SECONDS:
                throttle = int(throttle) + 1
                from app.jobs.queue import JobStopped, fence

                try:
                    current = fence(db, job_id, owner)
                    kept = {k: v for k, v in current.checkpoint.items() if k not in {"throttle", "throttle_at"}}
                    current.checkpoint = (
                        kept if throttle >= width else {**kept, "throttle": throttle, "throttle_at": time.time()}
                    )
                    db.commit()
                except JobStopped:
                    pass  # the next write of the job says it stopped
            width = min(width, max(1, int(throttle)))
    limit = budget_width(job_id)
    if limit is not None:
        width = min(width, max(1, limit))
    _last_width[job_id] = width
    return width


def job_share(job: Job, owner: str) -> Callable[[], Awaitable[int]]:
    """`width` for `in_parallel`: read again before each start (books starting, outages, budget)."""
    return functools.partial(blocking, job_parallelism, job.id, owner, job.provider_id)


def throttled(job_id: str, checkpoint: dict) -> dict:
    """What an outage adds to the checkpoint of a job running passages side by side: half its width."""
    last = _last_width.pop(job_id, 0) or int(checkpoint.get("throttle") or 0)
    if last <= 1:
        return {}
    return {"throttle": max(1, last // 2), "throttle_at": time.time()}


async def in_parallel(
    items: Iterable[Item],
    width: Callable[[], Awaitable[int]],
    launch: Callable[[Item], Awaitable[Coroutine[Any, Any, None] | None]],
) -> None:
    """Runs up to `width()` passages at once, started in book order; the width is read again before
    each start, so a book gives up part of the provider as soon as another book starts on it.

    `launch` does the bookkeeping of one item in order (skip, progress checkpoint) and returns the work
    to run, or None. The first failure cancels the others once they stop, then is raised; so does a
    pause, a lost lease or a shutdown, which cancels the task running this function. A cancelled
    passage is never marked finished: a resumed job starts it again from what it had saved.
    """
    running: set[asyncio.Task] = set()
    try:
        for item in items:
            work = await launch(item)
            if work is not None:
                running.add(asyncio.create_task(work))
                await _drain(running, await width() - 1)
        await _drain(running, 0)
    finally:
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)


async def _drain(running: set[asyncio.Task], limit: int) -> None:
    while len(running) > limit:
        done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        running -= done
        failures = [task.exception() for task in done if not task.cancelled() and task.exception()]
        if failures:
            raise failures[0]
