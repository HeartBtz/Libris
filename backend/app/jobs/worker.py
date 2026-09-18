import asyncio
import contextlib
import logging
import signal
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal
from app.diagnostics import safe_trace
from app.engines.context.config import memory_config
from app.engines.context.providers import OpenVikingContextProvider
from app.engines.memory.catalog import schedule_catalogs
from app.engines.translation.analysis import analyze
from app.engines.translation.pipeline import consistency, translate
from app.jobs.concurrency import blocking, renewal
from app.jobs.execution import execution
from app.jobs.queue import JobStopped, checkpoint, claim, emit, fence, suspend
from app.models import Issue, Job, Outbox, Project, Segment
from app.providers.llm import ProviderAuthenticationRequired, ProviderContentRefused, ProviderUnavailable

logger = logging.getLogger("epub.worker")


async def sync_outbox(project_id: str | None = None) -> None:
    if not memory_config()["base_url"]:
        return
    provider = OpenVikingContextProvider()
    with SessionLocal() as db:
        query = (
            select(Outbox)
            .join(Project, Project.id == Outbox.project_id)
            .where(
                Outbox.status != "sent",
                Outbox.next_attempt <= time.time(),
                Project.context_backend != "internal",
            )
            .order_by(Outbox.created_at)
        )
        if project_id:
            query = query.where(Outbox.project_id == project_id)
        events = list(db.scalars(query.limit(20)))
    for event in events:
        try:
            await provider.ingest(event)
            status, error = "sent", ""
        except Exception as exc:
            status, error = "error", f"OpenViking : {type(exc).__name__}"
        with SessionLocal() as db:
            current = db.get(Outbox, event.id)
            if current and current.payload == event.payload:
                current.status, current.error = status, error
                current.attempts += 1
                current.next_attempt = (
                    0 if status == "sent" else time.time() + min(3600, 2 ** min(current.attempts, 11))
                )
                db.commit()


HEARTBEAT_GRACE = 40  # seconds without a renewal before giving up; the lease itself lasts 60


async def heartbeat(job_id: str, owner: str, task: asyncio.Task, clock=time.monotonic) -> None:
    interval = settings().worker_heartbeat_seconds
    renewed = clock()
    while True:
        await asyncio.sleep(interval)
        try:
            await renewal(checkpoint, job_id, owner)
            renewed = clock()
        except JobStopped:
            task.cancel()
            return
        except SQLAlchemyError as exc:
            # The lease outlives a database blip: cancelling at once would throw away a model call
            # that is already paid for. Give up only when the lease can no longer be trusted.
            if clock() - renewed > HEARTBEAT_GRACE:
                task.cancel()
                return
            logger.warning("job=%s heartbeat=deferred reason=%s", job_id, type(exc).__name__)


def _suspend_safely(job_id: str, *arguments) -> None:
    """A recovery transition must never become the failure that takes the worker down."""
    try:
        suspend(job_id, *arguments)
    except SQLAlchemyError:
        # The lease expires on its own and the job is reclaimed from its checkpoint.
        logger.error("job=%s status=suspend_deferred reason=database_unavailable", job_id)


def _complete(job_id: str, owner: str, project_id: str) -> None:
    with SessionLocal() as db:
        current = fence(db, job_id, owner)
        current.status = "completed"
        current.next_attempt, current.outage_count, current.stop_reason = 0, 0, ""
        project = db.get(Project, project_id)
        incomplete = db.scalar(
            select(Segment.id).where(Segment.project_id == project.id, Segment.translation == "").limit(1)
        )
        project.status = "ready" if incomplete else "completed"
        emit(db, project.id, job_id=job_id, status="completed")
        db.commit()


async def execute(job_id: str, owner: str) -> None:
    try:
        job = await blocking(checkpoint, job_id, owner)
    except JobStopped:
        return
    except SQLAlchemyError:
        logger.error("job=%s status=database_unavailable step=claim", job_id)
        return
    scope = execution.set((job_id, owner))
    heart = asyncio.create_task(heartbeat(job_id, owner, asyncio.current_task()))
    try:
        if job.operation == "analyze":
            await analyze(job, owner)
            job = await blocking(checkpoint, job.id, owner)
            if job.options.get("continue_pipeline"):
                await translate(job, owner)
        elif job.operation in {"translate", "review"}:
            await translate(job, owner)
        elif job.operation == "consistency":
            await consistency(job, owner)
        elif job.operation == "resolve_validations":
            from app.engines.translation.final_review import resolve_validations

            await resolve_validations(job, owner)
        elif job.operation == "accept_critiques":
            from app.engines.translation.critique_queue import accept_queued_critiques

            await accept_queued_critiques(job, owner)
        else:
            await sync_outbox(job.project_id)
        await blocking(_complete, job_id, owner, job.project_id)
    except JobStopped:
        pass
    except ProviderUnavailable as exc:
        _suspend_safely(job_id, owner, "waiting", "provider_unavailable", str(exc), exc.retry_after)
    except ProviderAuthenticationRequired as exc:
        _suspend_safely(job_id, owner, "blocked", "authentication_required", str(exc))
    except ProviderContentRefused as exc:
        try:
            with SessionLocal() as db:
                current = fence(db, job_id, owner)
                sid = current.checkpoint.get("segment_id")
                if sid:
                    segment = db.get(Segment, sid)
                    if segment and not segment.human:
                        segment.status, segment.error = "refused", str(exc)
                db.add(
                    Issue(
                        project_id=job.project_id,
                        segment_id=sid,
                        severity="error",
                        code="content_refusal",
                        message=f"{job.operation} : {exc} Source conservée ; intervention humaine nécessaire.",
                    )
                )
                db.commit()
        except JobStopped:
            pass  # Paused, cancelled or reclaimed while the request was in flight: that decision wins.
        except SQLAlchemyError:
            logger.error("job=%s status=database_unavailable step=content_refusal", job_id)
        else:
            _suspend_safely(job_id, owner, "blocked", "content_refusal", str(exc))
    except asyncio.CancelledError:
        with contextlib.suppress(SQLAlchemyError):
            suspend(
                job_id,
                owner,
                "pending",
                "worker_interrupted",
                "Worker arrêté ; reprise au prochain démarrage.",
            )
        raise
    except SQLAlchemyError:
        logger.error("job=%s status=database_unavailable", job_id)
        with contextlib.suppress(SQLAlchemyError):
            suspend(
                job_id,
                owner,
                "waiting",
                "database_unavailable",
                "Base de données temporairement indisponible.",
            )
    except Exception as exc:
        logger.error(
            "job=%s project=%s status=failed error_type=%s trace=%s",
            job_id,
            job.project_id,
            type(exc).__name__,
            safe_trace(exc),
        )
        with contextlib.suppress(SQLAlchemyError), SessionLocal() as db:
            current = db.get(Job, job_id)
            if current and current.lease_owner == owner and current.status not in {"paused", "cancelled"}:
                current.status, current.error = "failed", str(exc)[:1500]
                db.get(Project, job.project_id).status = "failed"
                emit(db, job.project_id, job_id=job_id, status="failed", error=current.error)
                db.commit()
    finally:
        heart.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heart
        execution.reset(scope)


async def worker_slot(stopped: asyncio.Event, operations: tuple[str, ...] | None = None) -> None:
    failures = 0
    while not stopped.is_set():
        try:
            item = await blocking(claim, operations)
            if not item:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=2)
                except TimeoutError:
                    pass
                continue
            work = asyncio.create_task(execute(*item))
            shutdown = asyncio.create_task(stopped.wait())
            try:
                done, _ = await asyncio.wait({work, shutdown}, return_when=asyncio.FIRST_COMPLETED)
                if shutdown in done:
                    work.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await work
                    break
                try:
                    await work
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.error("operation=worker_slot status=task_failed trace=%s", safe_trace(exc))
            finally:
                shutdown.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await shutdown
            failures = 0
            delay = 0 if item else 2
        except SQLAlchemyError:
            failures += 1
            delay = min(2 ** min(failures, 5), 30)
            logger.error("operation=worker_database status=waiting retry_seconds=%s", delay)
        if delay:
            try:
                await asyncio.wait_for(stopped.wait(), timeout=delay)
            except TimeoutError:
                pass


async def provider_dispatcher(stopped: asyncio.Event) -> None:
    operations = ("analyze", "translate", "review", "consistency", "resolve_validations", "accept_critiques")
    running: set[asyncio.Task] = set()
    shutdown = asyncio.create_task(stopped.wait())
    try:
        while not stopped.is_set():
            try:
                while item := await blocking(claim, operations):
                    running.add(asyncio.create_task(execute(*item)))
            except SQLAlchemyError:
                logger.error("operation=provider_dispatch status=waiting retry_seconds=2")
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=2)
                except TimeoutError:
                    pass
                continue
            if not running:
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=2)
                except TimeoutError:
                    pass
                continue
            done, _ = await asyncio.wait(
                {*running, shutdown}, timeout=2, return_when=asyncio.FIRST_COMPLETED
            )
            if shutdown in done:
                break
            for task in done:
                if task is shutdown:
                    continue
                running.discard(task)
                # One job's unexpected failure must not stop every other book.
                if not task.cancelled() and task.exception() is not None:
                    logger.error(
                        "operation=provider_dispatch status=task_failed trace=%s",
                        safe_trace(task.exception()),
                    )
    finally:
        shutdown.cancel()
        for task in running:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown
        if running:
            await asyncio.gather(*running, return_exceptions=True)


def catalog_due(last: float, now: float) -> bool:
    return now - last > settings().memory_catalog_interval_seconds


async def memory_pump(stopped: asyncio.Event) -> None:
    last_catalog = 0.0
    while not stopped.is_set():
        if catalog_due(last_catalog, time.monotonic()):
            with contextlib.suppress(SQLAlchemyError):
                schedule_catalogs()
            last_catalog = time.monotonic()
        work = asyncio.create_task(sync_outbox())
        shutdown = asyncio.create_task(stopped.wait())
        try:
            done, _ = await asyncio.wait({work, shutdown}, return_when=asyncio.FIRST_COMPLETED)
            if shutdown in done:
                work.cancel()
            with contextlib.suppress(asyncio.CancelledError, SQLAlchemyError):
                await work
        finally:
            shutdown.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await shutdown
        try:
            await asyncio.wait_for(stopped.wait(), timeout=3)
        except TimeoutError:
            pass


RETENTION_INTERVAL = 3600


async def retention_loop(stopped: asyncio.Event) -> None:
    from app.maintenance.retention import apply

    while not stopped.is_set():
        try:
            # Off the event loop: a long purge must not delay heartbeats and lose job leases.
            result = await asyncio.to_thread(apply)
            if any(result.values()):
                logger.info("retention=%s", result)
        except SQLAlchemyError as exc:
            logger.warning("retention=deferred reason=%s", type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopped.wait(), timeout=RETENTION_INTERVAL)


async def main() -> None:
    settings().prepare()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    logger.info("provider_scoped_concurrency=enabled")
    await asyncio.gather(
        memory_pump(stopped),
        provider_dispatcher(stopped),
        worker_slot(stopped, ("sync_memory",)),
        retention_loop(stopped),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(main())
