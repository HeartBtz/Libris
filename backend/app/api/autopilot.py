"""What the autopilot decided for a book, and how its last run ended."""

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.common import row
from app.automation_settings import autopilot_config
from app.engines.budget import cost_report
from app.jobs.launch import autopilot_default
from app.models import AutopilotDecision, Job
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api")


def _filters(project_id: str, job_id: str | None, segment_id: str | None, stage: str | None) -> list:
    conditions = [AutopilotDecision.project_id == project_id]
    for column, value in (
        (AutopilotDecision.job_id, job_id),
        (AutopilotDecision.segment_id, segment_id),
        (AutopilotDecision.stage, stage),
    ):
        if value:
            conditions.append(column == value)
    return conditions


@router.get("/projects/{project_id}/autopilot")
def autopilot(
    project_id: str,
    user: CurrentUser,
    db: DB,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    job_id: str | None = None,
    segment_id: str | None = None,
    stage: str | None = Query(default=None, max_length=40),
):
    """Decisions, newest first, and the report of the last job that wrote one."""
    project = access(db, project_id, user)
    conditions = _filters(project_id, job_id, segment_id, stage)
    total = db.scalar(select(func.count(AutopilotDecision.id)).where(*conditions))
    decisions = db.scalars(
        select(AutopilotDecision)
        .where(*conditions)
        .order_by(AutopilotDecision.created_at.desc(), AutopilotDecision.id.desc())
        .limit(limit)
        .offset(offset)
    )
    jobs = db.scalars(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(20)
    )
    last = next((job for job in jobs if (job.result or {}).get("autopilot")), None)
    config = autopilot_config(db)
    return {
        "enabled": autopilot_default(project),
        "settings": {
            "max_rounds": config["max_rounds"],
            "fallback_provider_ids": project.config.get("fallback_provider_ids") or [],
            "outage_max_retries": config["outage_max_retries"],
            "outage_max_wait_seconds": config["outage_max_wait_seconds"],
        },
        "report": None
        if last is None
        else {
            **last.result["autopilot"],
            "job_id": last.id,
            "status": last.status,
            "finished_at": last.finished_at,
            # Estimated against real cost of that job (app.engines.budget).
            "cost": cost_report(db, last),
        },
        "decisions": {
            "items": [row(decision) for decision in decisions],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    }
