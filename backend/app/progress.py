from sqlalchemy import func, select

from app.jobs import segment_state as state
from app.models import Job, JobSegmentState, Project, Provider, RequestLog, Segment

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
    review_rows = (
        select(JobSegmentState.segment_id)
        .join(Job, Job.id == JobSegmentState.job_id)
        .where(
            Job.project_id == project.id,
            Job.operation.in_(("analyze", "translate", "resolve_validations")),
            JobSegmentState.step.in_((state.REVIEW_TARGET, state.REVIEWED)),
        )
    )
    # Newest job first: its outcome for a passage wins over an older review of the same passage.
    for sid, step, outcome, data in db.execute(
        review_rows.add_columns(JobSegmentState.step, JobSegmentState.outcome, JobSegmentState.data).order_by(
            Job.created_at.desc()
        )
    ):
        if step == state.REVIEW_TARGET:
            review_targets.add(sid)
            continue
        review_done_ids.add(sid)
        if outcome:
            outcomes.setdefault(sid, {**data, "outcome": outcome})
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
                Segment.project_id == project.id, Segment.id.in_(review_rows)
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
