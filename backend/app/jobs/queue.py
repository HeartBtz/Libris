import time

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db import SessionLocal
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
    now = time.time()
    with SessionLocal() as db:
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
                lease_until=now + 60,
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
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.lease_owner != owner or job.status not in RUNNING or job.lease_until < time.time():
            raise JobStopped()
        job.lease_until = time.time() + 60
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
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.lease_owner != owner or job.status not in RUNNING or job.lease_until < time.time():
        raise JobStopped()
    return job


def finish_segment(job_id: str, owner: str, segment_id: str) -> None:
    with SessionLocal() as db:
        job = fence(db, job_id, owner)
        completed = list(dict.fromkeys([*job.checkpoint.get("finished_ids", []), segment_id]))
        job.checkpoint = {**job.checkpoint, "finished_ids": completed}
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
