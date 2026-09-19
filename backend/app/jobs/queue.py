import time

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.jobs import clock
from app.jobs.concurrency import job_lock
from app.jobs.segment_state import FINISHED, mark
from app.models import AppSetting, Event, Job, Project, Provider, RequestLog
from app.models.common import uid

RUNNING = ("analyzing", "translating", "reviewing", "syncing")
ACTIVE = ("pending", "waiting", *RUNNING)
HELD = (*ACTIVE, "paused", "blocked")
STEP_STATUS = {
    "chapter_analysis": "analyzing",
    "book_bible": "analyzing",
    "translation": "translating",
    "automatic_recovery": "translating",
    "recovery_required": "translating",
    "final_review": "reviewing",
    "consistency": "reviewing",
    "critique_acceptance": "reviewing",
}


class JobStopped(Exception):
    pass


def emit(db: Session, project_id: str, **payload) -> None:
    db.add(Event(project_id=project_id, created_at=time.time(), payload=payload))


def enqueue(db: Session, project: Project, operation: str, options: dict) -> Job:
    # Project lock prevents two concurrent submissions from creating duplicate active pipelines.
    db.scalar(select(Project).where(Project.id == project.id).with_for_update())
    active = db.scalar(select(Job).where(Job.project_id == project.id, Job.status.in_(HELD)))
    if active:
        raise ValueError("Un travail existe déjà. Reprenez-le ou annulez-le avant d’en lancer un autre.")
    provider_id = None if operation == "sync_memory" else options.get("provider_id") or project.provider_id
    job = Job(project_id=project.id, provider_id=provider_id, operation=operation, options=options)
    db.add(job)
    db.flush()
    project.status = "pending"
    emit(db, project.id, operation=operation, status="pending", job_id=job.id)
    return job


def claim(operations: tuple[str, ...] | None = None) -> tuple[str, str] | None:
    with SessionLocal() as db:
        # Leases are on the database clock, shared by every worker (app/jobs/clock.py).
        now = clock.now(db)
        condition = or_(
            Job.status == "pending",
            and_(Job.status == "waiting", Job.next_attempt <= now),
            and_(Job.status.in_(RUNNING), Job.lease_until < now),
        )
        query = select(Job.id, Job.provider_id, Job.operation).where(condition)
        if operations:
            query = query.where(Job.operation.in_(operations))
        candidates = db.execute(query.order_by(Job.created_at)).all()
        job = None
        saturated: set[str] = set()
        for job_id, provider_id, operation in candidates:
            if provider_id:
                if provider_id in saturated:
                    continue
                provider = db.scalar(
                    select(Provider)
                    .where(Provider.id == provider_id)
                    .with_for_update(skip_locked=True)
                )
                if not provider:
                    continue
                active = db.scalar(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.provider_id == provider_id,
                        Job.status.in_(RUNNING),
                        Job.lease_until >= now,
                    )
                )
                if active >= provider.max_concurrency:
                    saturated.add(provider_id)
                    continue
            elif operation != "sync_memory":
                # Its provider was deleted, or never chosen: waiting would last forever and keep the
                # book locked. Say so; resuming re-reads the book's provider.
                orphan = db.scalar(
                    select(Job).where(Job.id == job_id, condition).with_for_update(skip_locked=True)
                )
                if orphan:
                    orphan.status, orphan.stop_reason = "blocked", "provider_missing"
                    orphan.error = (
                        "Aucun fournisseur n’est associé à ce travail. Choisissez un fournisseur pour "
                        "ce livre, puis reprenez le travail."
                    )
                    orphan.lease_owner, orphan.lease_until = "", 0
                    db.get(Project, orphan.project_id).status = "blocked"
                    emit(db, orphan.project_id, job_id=orphan.id, status="blocked", reason="provider_missing")
                    db.commit()
                continue
            job = db.scalar(
                select(Job)
                .where(Job.id == job_id, condition)
                .with_for_update(skip_locked=True)
            )
            if job:
                break
        if not job:
            return None
        owner = uid()
        state = {
            "analyze": "analyzing",
            "translate": "translating",
            "review": "reviewing",
            "consistency": "reviewing",
            "sync_memory": "syncing",
            "resolve_validations": "reviewing",
            "accept_critiques": "reviewing",
        }[job.operation]
        state = STEP_STATUS.get(job.checkpoint.get("step"), state)
        result = db.execute(
            update(Job)
            .where(Job.id == job.id, condition)
            .values(
                lease_owner=owner,
                lease_until=now + clock.LEASE_SECONDS,
                status=state,
                attempts=Job.attempts + 1,
                next_attempt=0,
                stop_reason="",
                error="",
            )
        )
        if result.rowcount != 1:
            return None
        db.execute(update(Project).where(Project.id == job.project_id).values(status=state))
        emit(db, job.project_id, job_id=job.id, status=state, operation=job.operation)
        db.commit()
        return job.id, owner


def checkpoint(job_id: str, owner: str, progress: dict | None = None) -> Job:
    if progress is None:
        return _checkpoint(job_id, owner, None)
    with job_lock(job_id):
        return _checkpoint(job_id, owner, progress)


def _checkpoint(job_id: str, owner: str, progress: dict | None) -> Job:
    with SessionLocal() as db:
        found = db.execute(select(Job, clock.database_now()).where(Job.id == job_id).with_for_update()).first()
        job, now = found if found else (None, 0)
        if not job or job.lease_owner != owner or job.status not in RUNNING or job.lease_until < now:
            raise JobStopped()
        job.lease_until = now + clock.LEASE_SECONDS
        if progress is not None:
            job.checkpoint = {**job.checkpoint, **progress}
            state = STEP_STATUS.get(progress.get("step"))
            if state and state != job.status:
                job.status = state
                db.get(Project, job.project_id).status = state
            emit(db, job.project_id, job_id=job.id, status=job.status, **progress)
        db.commit()
        return job


def fence(db: Session, job_id: str, owner: str) -> Job:
    found = db.execute(select(Job, clock.database_now()).where(Job.id == job_id).with_for_update()).first()
    job, now = found if found else (None, 0)
    if not job or job.lease_owner != owner or job.status not in RUNNING or job.lease_until < now:
        raise JobStopped()
    return job


def lock_live_jobs(db: Session, project_id: str, *, analysis: bool = True) -> list[str]:
    """Locks the book's held jobs before any passage row, in the worker's order (job, then passage).

    The worker holds its job row (`fence`) while it writes a passage; an API action that locked the
    passage first and then touched the job (recording the passage as settled) could deadlock with it.
    """
    query = select(Job.id).where(Job.project_id == project_id, Job.status.in_(HELD))
    if not analysis:
        query = query.where(Job.operation != "analyze")
    return list(db.scalars(query.order_by(Job.id).with_for_update()))


def finish_segment(job_id: str, owner: str, segment_id: str) -> None:
    with SessionLocal() as db:
        job = fence(db, job_id, owner)
        mark(db, job_id, FINISHED, segment_id)
        job.outage_count = 0
        db.commit()


def suspend(
    job_id: str, owner: str, status: str, reason: str, message: str = "", retry_after: float = 0
) -> None:
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        # An explicit user pause/cancel or a new owner always wins over automatic recovery.
        if not job or job.lease_owner != owner or job.status not in RUNNING:
            return
        if status == "waiting":
            job.outage_count += 1
            from app.config import settings
            from app.providers.reliability import calculate_retry_delay

            # Try user-configured recovery settings first, fall back to environment defaults
            saved = db.get(AppSetting, "provider_recovery")
            if saved and "retry_seconds" in saved.value:
                base_delay = saved.value["retry_seconds"]
            else:
                base_delay = settings().provider_recovery_base_seconds
            max_delay = settings().provider_recovery_max_seconds
            delay = calculate_retry_delay(job.outage_count, base_delay, max_delay, retry_after)
            job.next_attempt = time.time() + delay
        else:
            job.next_attempt = 0
        job.status, job.stop_reason, job.error = status, reason, message
        job.lease_owner, job.lease_until = "", 0
        db.get(Project, job.project_id).status = status
        db.execute(
            update(RequestLog)
            .where(
                RequestLog.job_id == job_id,
                RequestLog.execution_owner == owner,
                RequestLog.status == "running",
            )
            .values(status="interrupted", error="Exécution interrompue ; résultat non appliqué.")
        )
        emit(
            db,
            job.project_id,
            job_id=job_id,
            status=status,
            reason=reason,
            error=message,
            next_attempt=job.next_attempt,
        )
        db.commit()
