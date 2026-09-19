"""Provider fallback: an outage is waited out for a bounded time, then the next provider takes over.

The chain is the job's provider, then the project's `config["fallback_provider_ids"]`, then
AUTOPILOT_FALLBACK_PROVIDERS (names or ids). When no provider of the chain answers any more, the job
ends `failed` with the reason: waiting for ever would leave the book neither finished nor failed.
"""

import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.engines.autopilot.decisions import enabled, record
from app.jobs.queue import RUNNING, emit
from app.models import Job, Project, Provider, RequestLog


def chain(db: Session, project: Project, first: str | None = None) -> list[str]:
    """Existing providers, in the order they are tried; `first` (the job's provider) leads."""
    ids = [first, project.provider_id, *(project.config.get("fallback_provider_ids") or [])]
    for token in settings().autopilot_fallback_providers.split(","):
        if token := token.strip():
            found = db.get(Provider, token) or db.scalar(
                select(Provider).where(Provider.name == token).limit(1)
            )
            if found:
                ids.append(found.id)
    ids = [value for value in dict.fromkeys(ids) if isinstance(value, str) and value]
    existing = set(db.scalars(select(Provider.id).where(Provider.id.in_(ids))))
    return [value for value in ids if value in existing]


def fallbacks(project_id: str, provider_id: str | None) -> list[str]:
    """The providers a failed passage may still be tried with, besides the job's own one."""
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        return [value for value in chain(db, project, provider_id) if value != provider_id]


def failed_report(job: Job, reason: str) -> dict:
    return {
        "outcome": "failed",
        "rounds": int(job.checkpoint.get("autopilot_rounds_done", 0)),
        "residuals": [],
        "reason": reason,
    }


def handle_outage(job_id: str, owner: str, message: str, authentication: bool) -> bool:
    """Called when the job's provider is down or refuses its credentials. False: wait as usual."""
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.lease_owner != owner or job.status not in RUNNING or not enabled(job):
            return False
        now = time.time()
        progress = dict(job.checkpoint)
        started = progress.get("outage_started_at") if job.outage_count else None
        started = started or now
        config = settings()
        if (
            not authentication
            and job.outage_count < config.autopilot_outage_max_retries
            and now - started < config.autopilot_outage_max_wait_seconds
        ):
            job.checkpoint = {**progress, "outage_started_at": started}
            db.commit()
            return False
        project = db.get(Project, job.project_id)
        failing = db.get(Provider, job.provider_id) if job.provider_id else None
        tried = [value for value in progress.get("autopilot_providers_tried") or [] if value]
        tried = list(dict.fromkeys([*tried, job.provider_id]))
        following = next((value for value in chain(db, project, job.provider_id) if value not in tried), None)
        name = failing.name if failing else "?"
        why = (
            f"« {name} » refuse ses identifiants ({message[:300]})"
            if authentication
            else f"« {name} » indisponible après {job.outage_count + 1} tentatives ({message[:300]})"
        )
        kind = "authentication" if authentication else "outage"
        if following:
            replacement = db.get(Provider, following)
            record(
                db,
                project.id,
                job_id=job.id,
                stage="provider",
                kind=kind,
                action="fallback_provider",
                reason=f"{why} ; le travail continue avec « {replacement.name} ».",
                provider=replacement,
            )
            progress.pop("outage_started_at", None)
            job.checkpoint = {**progress, "autopilot_providers_tried": [*tried, following]}
            job.provider_id, job.outage_count = following, 0
            job.status, job.stop_reason, job.error, job.next_attempt = "pending", "provider_fallback", why, 0
            project.status = "pending"
        else:
            reason = f"{why} ; aucun autre fournisseur ne répond : travail arrêté."
            record(
                db,
                project.id,
                job_id=job.id,
                stage="provider",
                kind=kind,
                action="failed",
                reason=reason,
                provider=failing,
            )
            job.status, job.stop_reason, job.error = "failed", "providers_exhausted", reason
            job.finished_at, job.next_attempt = now, 0
            job.result = {**(job.result or {}), "autopilot": failed_report(job, reason)}
            project.status = "failed"
        job.lease_owner, job.lease_until = "", 0
        db.execute(
            update(RequestLog)
            .where(
                RequestLog.job_id == job_id,
                RequestLog.execution_owner == owner,
                RequestLog.status == "running",
            )
            .values(status="interrupted", error="Exécution interrompue ; résultat non appliqué.")
        )
        emit(db, project.id, job_id=job.id, status=job.status, reason=job.stop_reason, error=job.error)
        db.commit()
        return True
