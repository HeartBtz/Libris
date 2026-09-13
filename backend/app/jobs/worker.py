import asyncio
import contextlib
import logging
import signal
import time

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal
from app.engines.context.config import memory_config
from app.engines.context.providers import OpenVikingContextProvider
from app.engines.memory.catalog import schedule_catalogs
from app.engines.translation.analysis import analyze
from app.engines.translation.pipeline import consistency, translate
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


async def heartbeat(job_id: str, owner: str, task: asyncio.Task) -> None:
    while True:
        await asyncio.sleep(2)
        try:
            checkpoint(job_id, owner)
        except (JobStopped, SQLAlchemyError):
            task.cancel()
            return


async def execute(job_id: str, owner: str) -> None:
    try:
        job = checkpoint(job_id, owner)
    except JobStopped:
        return
    scope = execution.set((job_id, owner))
    heart = asyncio.create_task(heartbeat(job_id, owner, asyncio.current_task()))
    try:
        if job.operation == "analyze":
            await analyze(job, owner)
        elif job.operation in {"translate", "review"}:
            await translate(job, owner)
        elif job.operation == "consistency":
            await consistency(job, owner)
        elif job.operation == "resolve_validations":
            from app.engines.translation.final_review import resolve_validations

            await resolve_validations(job, owner)
        else:
            await sync_outbox(job.project_id)
        with SessionLocal() as db:
            current = fence(db, job_id, owner)
            current.status = "completed"
            current.next_attempt, current.outage_count, current.stop_reason = 0, 0, ""
            project = db.get(Project, job.project_id)
            incomplete = db.scalar(
                select(Segment.id).where(Segment.project_id == project.id, Segment.translation == "").limit(1)
            )
            project.status = "ready" if incomplete else "completed"
            emit(db, project.id, job_id=job_id, status="completed")
            db.commit()
    except JobStopped:
        pass
    except ProviderUnavailable as exc:
        suspend(job_id, owner, "waiting", "provider_unavailable", str(exc), exc.retry_after)
    except ProviderAuthenticationRequired as exc:
        suspend(job_id, owner, "blocked", "authentication_required", str(exc))
    except ProviderContentRefused as exc:
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
        suspend(job_id, owner, "blocked", "content_refusal", str(exc))
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
            "job=%s project=%s status=failed error_type=%s", job_id, job.project_id, type(exc).__name__
        )
        with SessionLocal() as db:
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
            item = claim(operations)
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
                with contextlib.suppress(asyncio.CancelledError):
                    await work
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
    operations = ("analyze", "translate", "review", "consistency", "resolve_validations")
    running: set[asyncio.Task] = set()
    shutdown = asyncio.create_task(stopped.wait())
    try:
        while not stopped.is_set():
            try:
                while item := claim(operations):
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
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    finally:
        shutdown.cancel()
        for task in running:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await shutdown
        if running:
            await asyncio.gather(*running, return_exceptions=True)


async def memory_pump(stopped: asyncio.Event) -> None:
    last_catalog = 0.0
    while not stopped.is_set():
        if time.monotonic() - last_catalog > 60:
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
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(main())
