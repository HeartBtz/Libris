"""The end of an automation request: always a terminal status, with a report and, on success, a result.

completed: every passage translated; completed_with_residuals: some passages kept their source (listed
in the report); failed: with the reason; cancelled. A job that stays paused, blocked or waiting beyond
API_REQUEST_STALL_MINUTES, or a request unfinished after API_REQUEST_MAX_HOURS, is cancelled and the
request fails with the reason: no request stays `running` forever.

The worker's request dispatcher calls `settle` on each running request; the API calls it too before
answering, so a client never waits for the dispatcher's next pass. The caller holds the row lock and
commits.
"""

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.engines.delivery.epub import DeliveryFailed
from app.engines.delivery.report import autopilot_report, build_report
from app.engines.delivery.results import default_format, render, store
from app.jobs.queue import HELD
from app.models import Job, Project, TranslationRequest

logger = logging.getLogger("epub.delivery")

SUCCESS = ("completed", "completed_with_residuals")
FINAL = (*SUCCESS, "failed", "cancelled")
# Statuses after which nothing more happens to a request (imported: its chapters, without a job).
ENDED = (*FINAL, "imported")
STALLED = ("paused", "blocked", "waiting")


def finalize(
    request: TranslationRequest, status: str, error: str = "", report: dict | None = None,
    artifact: dict | None = None,
) -> None:  # fmt: skip
    """The one place a request ends: the webhook, if any, is queued here for the worker."""
    request.status, request.error = status, (error or "")[:1500]
    request.finished_at = time.time()
    request.report = report
    request.artifact = artifact
    # Kept for the token budgets, which still count it once the model calls are purged.
    request.cost = float((report.get("usage") or {}).get("cost") or 0) if report else None
    if request.callback_url:
        request.webhook_state, request.webhook_attempts, request.webhook_next_attempt = "pending", 0, 0
        request.webhook_error = ""
    logger.info("request=%s status=%s", request.id, status)


def fail(db: Session, request: TranslationRequest, reason: str, job: Job | None = None) -> None:
    project = db.get(Project, request.project_id or "")
    finalize(request, "failed", reason, build_report(db, request, project, job, "failed", reason))


def stop_job(db: Session, job: Job) -> None:
    """Cancels a job held too long, so that the volume is free for the next request."""
    from app.api.projects import control_job

    project = db.get(Project, job.project_id)
    if project is not None and job.status in HELD:
        control_job(db, project, job, "cancel")
        job.stop_reason = "request_deadline"


def stall_message(job: Job, minutes: int) -> str:
    detail = job.error or job.stop_reason or job.status
    return f"Travail resté « {job.status} » plus de {minutes} min sans reprendre : {detail}"


def deadline_message(hours: int) -> str:
    return f"Requête toujours inachevée après {hours} h : délai maximal dépassé."


def watch(db: Session, request: TranslationRequest, job: Job, now: float) -> bool:
    """Bounds the life of a request whose job does not end; True when the request ended here."""
    limits = settings()
    since = request.options.get("stalled_since")
    if job.status in STALLED:
        if not since:
            request.options = {**request.options, "stalled_since": now}
        elif now - since > limits.api_request_stall_minutes * 60:
            reason = stall_message(job, limits.api_request_stall_minutes)
            stop_job(db, job)
            fail(db, request, reason, job)
            return True
    elif since:
        request.options = {key: value for key, value in request.options.items() if key != "stalled_since"}
    if now - request.created_at > limits.api_request_max_hours * 3600:
        stop_job(db, job)
        fail(db, request, deadline_message(limits.api_request_max_hours), job)
        return True
    return False


def deliver(db: Session, request: TranslationRequest, job: Job) -> None:
    """The job completed: build and store the result, then end the request with its report."""
    project = db.get(Project, request.project_id or "")
    if project is None:
        fail(db, request, "Le volume de cette requête a été supprimé.", job)
        return
    autopilot = autopilot_report(job)
    if autopilot and autopilot.get("outcome") == "failed":
        fail(db, request, str(autopilot.get("reason") or "L’autopilote a échoué."), job)
        return
    fmt = default_format(request)
    delivery = None
    rendered = None
    try:
        if fmt == "epub":
            rendered = render(db, request, project, job, fmt, "completed", None)
            delivery = rendered.delivery
    except DeliveryFailed as exc:
        report = build_report(db, request, project, job, "failed", exc.reason)
        report["delivery"] = {"errors": exc.details}
        finalize(request, "failed", exc.reason, report)
        return
    report = build_report(db, request, project, job, "completed", delivery=delivery)
    outcome = "completed_with_residuals" if report["residual_total"] else "completed"
    report["outcome"] = outcome
    if rendered is None:
        rendered = render(db, request, project, job, fmt, outcome, report, bundle_report=True)
    finalize(request, outcome, "", report, store(request, rendered))


def settle(db: Session, request: TranslationRequest, now: float | None = None) -> None:
    """Moves a running request on from its job's state; the caller holds the row lock and commits."""
    if request.status != "running":
        return
    now = now or time.time()
    job = db.get(Job, request.job_id or "")
    if job is None:
        fail(db, request, "Le travail de cette requête a été supprimé.")
    elif job.status == "completed":
        deliver(db, request, job)
    elif job.status == "failed":
        fail(db, request, job.error or "Le travail de cette requête a échoué.", job)
    elif job.status == "cancelled":
        project = db.get(Project, request.project_id or "")
        reason = "Travail annulé."
        finalize(request, "cancelled", reason, build_report(db, request, project, job, "cancelled", reason))
    else:
        watch(db, request, job, now)


def refresh(db: Session, request_id: str) -> None:
    """Settles a request from the API before answering; skipped when the dispatcher holds it."""
    query = select(TranslationRequest).where(
        TranslationRequest.id == request_id, TranslationRequest.status == "running"
    )
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    request = db.scalar(query.execution_options(populate_existing=True))
    if request is None:
        return
    try:
        settle(db, request)
        db.commit()
    except BaseException:
        db.rollback()
        raise
