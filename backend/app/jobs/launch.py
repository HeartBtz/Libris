"""Starting the work of a freshly imported volume, when the person (or the API client) asked for it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jobs.queue import HELD, enqueue
from app.models import Job, Project

# The whole pipeline: analysis, then translation, automatic recovery and the final review.
PIPELINE = {"continue_pipeline": True, "automatic_recovery": True, "full_review": True}


def launch(db: Session, project: Project, mode: str) -> tuple[Job | None, str]:
    """`mode` is "analyze" or "pipeline". Returns the job, or none and why nothing was started."""
    if project.archived_at is not None:
        return None, f"« {project.title} » est archivé : aucun travail lancé."
    if not project.provider_id:
        return None, f"Aucun provider pour « {project.title} » : choisissez-en un, puis lancez l’analyse."
    held = db.scalar(select(Job).where(Job.project_id == project.id, Job.status.in_(HELD)))
    if held:
        return held, ""
    return enqueue(db, project, "analyze", dict(PIPELINE) if mode == "pipeline" else {}), ""
