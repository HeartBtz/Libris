"""Starting the work of a freshly imported volume, when the person (or the API client) asked for it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.jobs.queue import HELD, enqueue
from app.models import Job, Project

# The whole pipeline: analysis, then translation, automatic recovery and the final review.
PIPELINE = {"continue_pipeline": True, "automatic_recovery": True, "full_review": True}
# What the autopilot adds to a whole-book job: no step waits for a person (see app.engines.autopilot).
AUTOPILOT = {"autopilot": True, "automatic_recovery": True, "full_review": True}


def autopilot_default(project: Project) -> bool:
    """The project's own choice (`config["autopilot"]`), else AUTOPILOT_ENABLED (on by default)."""
    chosen = (project.config or {}).get("autopilot")
    return settings().autopilot_enabled if chosen is None else bool(chosen)


def pipeline_options(project: Project) -> dict:
    return {**PIPELINE, **(AUTOPILOT if autopilot_default(project) else {})}


def launch(db: Session, project: Project, mode: str, options: dict | None = None) -> tuple[Job | None, str]:
    """`mode` is "analyze" or "pipeline"; `options` are added to the new job's (e.g. `final_review`).
    Returns the job, or none and why nothing was started."""
    if project.archived_at is not None:
        return None, f"« {project.title} » est archivé : aucun travail lancé."
    if not project.provider_id:
        return None, f"Aucun provider pour « {project.title} » : choisissez-en un, puis lancez l’analyse."
    held = db.scalar(select(Job).where(Job.project_id == project.id, Job.status.in_(HELD)))
    if held:
        return held, ""
    base = pipeline_options(project) if mode == "pipeline" else {}
    if mode != "pipeline" and autopilot_default(project):
        base = {"autopilot": True}  # an analysis alone: a refused passage is skipped, not blocking
    return enqueue(db, project, "analyze", {**base, **(options or {})}), ""
