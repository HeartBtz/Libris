"""The project list as computed up to v0.4.1, kept verbatim as the reference of the list contract.

`GET /api/projects` now aggregates every book in a constant number of queries; its output must stay
identical to this per-book computation (without the bible, which the list no longer carries).
"""

from sqlalchemy import func, select

from app.api.common import row
from app.models import Chapter, Glossary, Job, Memory, Project, Provider, RequestLog, Segment

HELD = ("pending", "waiting", "analyzing", "translating", "reviewing", "syncing", "paused", "blocked")


STAGE_OPERATIONS = {
    "analysis": ("chapter_analysis", "book_analysis"),
    "translation": ("translation", "translation_review", "polishing"),
    "review": ("final_review", "translation_revision", "consistency_check"),
}


def _percent(done: int, total: int) -> int:
    return min(100, round(done / total * 100)) if total else 0


def _estimate(db, project: Project, stage: str, done: int, total: int) -> dict:
    operations = STAGE_OPERATIONS.get(stage, ())
    spent_cost = db.scalar(
        select(
            func.sum(
                (
                    RequestLog.prompt_tokens * Provider.input_cost
                    + RequestLog.completion_tokens * Provider.output_cost
                )
                / 1_000_000
            )
        )
        .join(Provider, RequestLog.provider_id == Provider.id)
        .where(RequestLog.project_id == project.id, RequestLog.status == "success")
    ) or 0
    if not operations or not done or done >= total:
        return {
            "remaining_seconds": 0 if done >= total else None,
            "remaining_cost": 0 if done >= total else None,
            "spent_cost": spent_cost,
            "confidence": "insufficient" if not done else "complete",
        }
    requests, duration, stage_cost = db.execute(
        select(
            func.count(),
            func.sum(RequestLog.duration),
            func.sum(
                (
                    RequestLog.prompt_tokens * Provider.input_cost
                    + RequestLog.completion_tokens * Provider.output_cost
                )
                / 1_000_000
            ),
        )
        .select_from(RequestLog)
        .join(Provider, RequestLog.provider_id == Provider.id)
        .where(
            RequestLog.project_id == project.id,
            RequestLog.operation.in_(operations),
            RequestLog.status == "success",
        )
    ).one()
    requests, duration, stage_cost = requests or 0, duration or 0, stage_cost or 0
    if not requests:
        return {
            "remaining_seconds": None,
            "remaining_cost": None,
            "spent_cost": spent_cost,
            "confidence": "insufficient",
        }
    remaining_requests = (total - done) * requests / done
    return {
        "remaining_seconds": round(remaining_requests * duration / requests),
        "remaining_cost": remaining_requests * stage_cost / requests,
        "spent_cost": spent_cost,
        "confidence": "high" if done >= 20 else "medium" if done >= 5 else "low",
    }


def project_progress(db, project: Project, stats: dict) -> dict:
    active_job = db.scalar(
        select(Job)
        .where(Job.project_id == project.id, Job.status.in_(HELD))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    job = active_job or db.scalar(
        select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(1)
    )
    checkpoint = job.checkpoint if job else {}
    analysis_done = stats["analyzed_segments"] + stats["synthesized_chapters"]
    analysis_total = stats["total"] + stats["chapters"]
    translation_started = bool(stats["translated"] or stats["errors"] or stats["refused"])
    review_targets: set[str] = set()
    review_done_ids: set[str] = set()
    outcomes: dict[str, dict] = {}
    review_checkpoints = db.scalars(
        select(Job.checkpoint)
        .where(
            Job.project_id == project.id,
            Job.operation.in_(("analyze", "translate", "resolve_validations")),
        )
        .order_by(Job.created_at.desc())
    )
    for candidate_checkpoint in review_checkpoints:
        review_targets.update(candidate_checkpoint.get("final_review_targets", []))
        review_done_ids.update(candidate_checkpoint.get("final_review_done", []))
        for sid, value in candidate_checkpoint.get("final_review_outcomes", {}).items():
            outcomes.setdefault(sid, value)
    if review_targets:
        review_done_ids.intersection_update(review_targets)
    review_done = len(review_done_ids) or stats["reviewed_segments"]
    review_total = len(review_targets) or stats["review_total"]
    active_review = bool(
        active_job
        and (
            checkpoint.get("step") in {"final_review", "critique_acceptance"}
            or active_job.operation in {"review", "consistency", "resolve_validations", "accept_critiques"}
        )
        and checkpoint.get("total")
    )
    review_stage_done = int(checkpoint.get("current", 0)) if active_review else review_done
    review_stage_total = int(checkpoint["total"]) if active_review else review_total
    export_ready = bool(
        stats["total"]
        and stats["translated"] == stats["total"]
        and not (stats["flagged"] or stats["errors"] or stats["refused"])
    )
    stages = [
        {"key": "import", "label": "Import", "done": 1, "total": 1, "percent": 100},
        {
            "key": "analysis",
            "label": "Analyse & mémoire",
            "done": analysis_done,
            "total": analysis_total,
            "percent": _percent(analysis_done, analysis_total),
        },
        {
            "key": "translation",
            "label": "Traduction",
            "done": stats["translated"],
            "total": stats["total"],
            "percent": _percent(stats["translated"], stats["total"]),
        },
        {
            "key": "review",
            "label": "Relecture",
            "done": review_stage_done,
            "total": review_stage_total,
            "percent": _percent(review_stage_done, review_stage_total),
        },
        {
            "key": "export",
            "label": "Export",
            "done": int(export_ready),
            "total": 1,
            "percent": 100 if export_ready else 0,
        },
    ]
    step = checkpoint.get("step", "")
    if active_job and (
        step == "final_review"
        or active_job.operation in {"review", "consistency", "resolve_validations", "accept_critiques"}
    ):
        active = "review"
    elif active_job and (
        step in {"translation", "automatic_recovery", "recovery_required"}
        or (
            active_job.operation == "translate"
            and (stats["translated"] < stats["total"] or stats["errors"] or stats["refused"])
        )
    ):
        active = "translation"
    elif active_job and (
        active_job.operation == "analyze" or step in {"chapter_analysis", "book_bible"}
    ):
        active = "analysis"
    elif not translation_started and analysis_done < analysis_total:
        active = "analysis"
    elif stats["translated"] < stats["total"] or stats["errors"] or stats["refused"]:
        active = "translation"
    elif not export_ready:
        active = "review"
    else:
        active = "export"
    current = next(stage for stage in stages if stage["key"] == active)
    provider_id = job.provider_id if job else project.provider_id
    model = db.scalar(select(Provider.model).where(Provider.id == provider_id)) if provider_id else None
    statuses = {
        sid: (status, human, validated)
        for sid, status, human, validated in db.execute(
            select(Segment.id, Segment.status, Segment.human, Segment.validated).where(
                Segment.project_id == project.id,
                Segment.id.in_(review_targets or review_done_ids),
            )
        )
    }
    outcome_for = {
        sid: outcomes.get(sid, {}).get("outcome")
        or (
            "protected"
            if sid in statuses and (statuses[sid][1] or statuses[sid][2])
            else "resolved"
            if sid in statuses and statuses[sid][0] == "ok"
            else "needs_human"
        )
        for sid in review_done_ids
    }
    review = {
        "examined": len(review_done_ids),
        "total": review_total,
        "resolved": sum(value == "resolved" for value in outcome_for.values()),
        "needs_human": sum(value == "needs_human" for value in outcome_for.values()),
        "remaining": stats["flagged"],
        "protected": sum(value == "protected" for value in outcome_for.values()),
        "revised": sum(1 for value in outcomes.values() if value.get("revised")),
        "failed": sum(value == "failed" for value in outcome_for.values()),
    }
    return {
        "active_stage": active,
        "state": job.status if job else project.status,
        "operation": job.operation if job else None,
        "job_id": job.id if job else None,
        "next_attempt": job.next_attempt if job else 0,
        "stop_reason": job.stop_reason if job else "",
        "model": model,
        "current": current,
        "stages": stages,
        "review": review,
        "estimate": _estimate(db, project, active, current["done"], current["total"]),
    }


def stats(db, project: Project) -> dict:
    total, done, validated, flagged, errors, refused = db.execute(
        select(
            func.count(Segment.id),
            func.count(Segment.id).filter(Segment.translation != "", Segment.retained_source.is_(False)),
            func.count(Segment.id).filter(Segment.validated.is_(True)),
            func.count(Segment.id).filter(Segment.status == "check"),
            func.count(Segment.id).filter(Segment.status == "error"),
            func.count(Segment.id).filter(Segment.status == "refused"),
        ).where(Segment.project_id == project.id)
    ).one()
    review_job = next(
        (
            candidate
            for candidate in db.scalars(
                select(Job)
                .where(
                    Job.project_id == project.id,
                    Job.operation.in_(["translate", "resolve_validations"]),
                )
                .order_by(Job.created_at.desc())
            )
            if candidate.checkpoint.get("step") == "final_review"
            or candidate.checkpoint.get("final_review_targets")
            or candidate.checkpoint.get("final_review_done")
        ),
        None,
    )
    review_checkpoint = review_job.checkpoint if review_job else {}
    review_done = len(set(review_checkpoint.get("final_review_done", [])))
    review_total = int(
        review_checkpoint.get("total")
        or len(review_checkpoint.get("final_review_targets", []))
        or flagged
        or total
    )
    return {
        "reviewed_segments": review_done,
        "review_total": review_total,
        "analyzed_segments": db.scalar(
            select(func.count(func.distinct(Memory.segment_id))).where(
                Memory.project_id == project.id, Memory.kind == "analysis"
            )
        ),
        "synthesized_chapters": db.scalar(
            select(func.count(Chapter.id)).where(Chapter.project_id == project.id, Chapter.analyzed.is_(True))
        ),
        "total": total,
        "retained_source": db.scalar(
            select(func.count(Segment.id)).where(
                Segment.project_id == project.id, Segment.retained_source.is_(True)
            )
        ),
        "translated": done,
        "validated": validated,
        "flagged": flagged,
        "errors": errors,
        "refused": refused,
        "chapters": db.scalar(
            select(func.count()).select_from(Chapter).where(Chapter.project_id == project.id)
        ),
        "glossary": db.scalar(
            select(func.count()).select_from(Glossary).where(Glossary.project_id == project.id)
        ),
    }


def legacy_list_item(db, project: Project) -> dict:
    values = stats(db, project)
    return dict(
        row(project, ("original_path", "bible")),
        stats=values,
        progress=project_progress(db, project, values),
    )
