"""Starting the work of a freshly imported volume, when the person (or the API client) asked for it."""

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.automation_settings import autopilot_config
from app.jobs.queue import HELD, enqueue
from app.models import ApiToken, Job, Project

# The whole pipeline: analysis, then translation, automatic recovery and the final review.
PIPELINE = {"continue_pipeline": True, "automatic_recovery": True, "full_review": True}
# What the autopilot adds to a whole-book job: no step waits for a person (see app.engines.autopilot).
AUTOPILOT = {"autopilot": True, "automatic_recovery": True, "full_review": True}


def autopilot_default(project: Project) -> bool:
    """The project's own choice (`config["autopilot"]`), else the global one (on by default)."""
    chosen = (project.config or {}).get("autopilot")
    if chosen is not None:
        return bool(chosen)
    return bool(autopilot_config(object_session(project))["enabled"])


def pipeline_options(project: Project) -> dict:
    return {**PIPELINE, **(AUTOPILOT if autopilot_default(project) else {})}


def launch(
    db: Session,
    project: Project,
    mode: str,
    options: dict | None = None,
    *,
    priority: int = 1,
    token_id: str | None = None,
) -> tuple[Job | None, str]:
    """`mode` is "analyze" or "pipeline"; `options` are added to the new job's (e.g. `final_review`);
    `priority` and `token_id` place it in the fair queue (app.jobs.fairness).
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
    options = {**base, **(options or {})}
    # Cost budgets of the book and of the API token that asked (app.engines.budget).
    from app.engines.budget import admit, request_token

    token = db.get(ApiToken, token_id) if token_id else request_token(db, options.get("translation_request"))
    estimated = ("analyze", "translate") if mode == "pipeline" else ("analyze",)
    refusal, kept = admit(db, project, estimated, token)
    if refusal:
        return None, refusal
    if kept:
        options["budget"] = kept
    return enqueue(db, project, "analyze", options, priority=priority, token_id=token_id), ""
