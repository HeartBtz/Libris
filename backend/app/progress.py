"""Book statistics and progress, computed for many books at once.

The project list is polled every few seconds: every figure comes from grouped queries whose
number does not depend on how many books are listed.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.jobs import segment_state as state
from app.models import Chapter, Glossary, Job, JobSegmentState, Memory, Project, Provider, RequestLog, Segment

HELD = ("pending", "waiting", "analyzing", "translating", "reviewing", "syncing", "paused", "blocked")


STAGE_OPERATIONS = {
    "analysis": ("chapter_analysis", "book_analysis"),
    "translation": ("translation", "translation_review", "polishing"),
    "review": ("final_review", "translation_revision", "consistency_check"),
}
REVIEW_JOB_OPERATIONS = ("translate", "resolve_validations")
REVIEW_STATE_OPERATIONS = ("analyze", "translate", "resolve_validations")
REVIEW_STEPS = (state.REVIEW_TARGET, state.REVIEWED)
ACTIVE_REVIEW_OPERATIONS = {"review", "consistency", "resolve_validations", "accept_critiques"}


def _percent(done: int, total: int) -> int:
    return min(100, round(done / total * 100)) if total else 0


@dataclass
class BookFacts:
    stats: dict = field(default_factory=dict)
    jobs: list[Job] = field(default_factory=list)  # most recent first
    spent_cost: float = 0
    stage_requests: dict = field(default_factory=dict)  # stage -> (requests, duration, cost)


def _cost():
    return (
        RequestLog.prompt_tokens * Provider.input_cost + RequestLog.completion_tokens * Provider.output_cost
    ) / 1_000_000


def book_facts(db, project_ids: list[str]) -> dict[str, BookFacts]:
    facts = {pid: BookFacts() for pid in project_ids}
    if not project_ids:
        return facts
    segments = {
        pid: values
        for pid, *values in db.execute(
            select(
                Segment.project_id,
                func.count(Segment.id),
                func.count(Segment.id).filter(Segment.translation != "", Segment.retained_source.is_(False)),
                func.count(Segment.id).filter(Segment.validated.is_(True)),
                func.count(Segment.id).filter(Segment.status == "check"),
                func.count(Segment.id).filter(Segment.status == "error"),
                func.count(Segment.id).filter(Segment.status == "refused"),
                func.count(Segment.id).filter(Segment.retained_source.is_(True)),
            )
            .where(Segment.project_id.in_(project_ids))
            .group_by(Segment.project_id)
        )
    }
    analyzed = dict(
        db.execute(
            select(Memory.project_id, func.count(func.distinct(Memory.segment_id)))
            .where(Memory.project_id.in_(project_ids), Memory.kind == "analysis")
            .group_by(Memory.project_id)
        ).all()
    )
    chapters = {
        pid: (count, synthesized)
        for pid, count, synthesized in db.execute(
            select(
                Chapter.project_id,
                func.count(Chapter.id),
                func.count(Chapter.id).filter(Chapter.analyzed.is_(True)),
            )
            .where(Chapter.project_id.in_(project_ids))
            .group_by(Chapter.project_id)
        )
    }
    glossary = dict(
        db.execute(
            select(Glossary.project_id, func.count(Glossary.id))
            .where(Glossary.project_id.in_(project_ids))
            .group_by(Glossary.project_id)
        ).all()
    )
    for job in db.scalars(
        select(Job).where(Job.project_id.in_(project_ids)).order_by(Job.created_at.desc())
    ):
        facts[job.project_id].jobs.append(job)
    # Per job: does it hold final review rows, and how many passages has it reviewed.
    review_rows = {
        job_id: (rows, reviewed)
        for job_id, rows, reviewed in db.execute(
            select(
                JobSegmentState.job_id,
                func.count(),
                func.count().filter(JobSegmentState.step == state.REVIEWED),
            )
            .join(Job, Job.id == JobSegmentState.job_id)
            .where(
                Job.project_id.in_(project_ids),
                Job.operation.in_(REVIEW_JOB_OPERATIONS),
                JobSegmentState.step.in_(REVIEW_STEPS),
            )
            .group_by(JobSegmentState.job_id)
        )
    }
    success = [RequestLog.project_id.in_(project_ids), RequestLog.status == "success"]
    stage_columns = []
    for operations in STAGE_OPERATIONS.values():
        chosen = RequestLog.operation.in_(operations)
        stage_columns += [
            func.count().filter(chosen),
            func.sum(RequestLog.duration).filter(chosen),
            func.sum(_cost()).filter(chosen),
        ]
    for pid, spent, *stage_values in db.execute(
        select(RequestLog.project_id, func.sum(_cost()), *stage_columns)
        .join(Provider, RequestLog.provider_id == Provider.id)
        .where(*success)
        .group_by(RequestLog.project_id)
    ):
        facts[pid].spent_cost = spent or 0
        facts[pid].stage_requests = {
            stage: tuple(value or 0 for value in stage_values[3 * index : 3 * index + 3])
            for index, stage in enumerate(STAGE_OPERATIONS)
        }
    for pid in project_ids:
        total, done, validated, flagged, errors, refused, retained = segments.get(pid, (0,) * 7)
        chapter_count, synthesized = chapters.get(pid, (0, 0))
        review_job = next(
            (
                job
                for job in facts[pid].jobs
                if job.operation in REVIEW_JOB_OPERATIONS
                and (job.id in review_rows or job.checkpoint.get("step") == "final_review")
            ),
            None,
        )
        review_checkpoint = review_job.checkpoint if review_job else {}
        facts[pid].stats = {
            "reviewed_segments": review_rows.get(review_job.id, (0, 0))[1] if review_job else 0,
            "review_total": int(
                review_checkpoint.get("total")
                or review_checkpoint.get("review_targets")
                or flagged
                or total
            ),
            "analyzed_segments": analyzed.get(pid, 0),
            "synthesized_chapters": synthesized,
            "total": total,
            "retained_source": retained,
            "translated": done,
            "validated": validated,
            "flagged": flagged,
            "errors": errors,
            "refused": refused,
            "chapters": chapter_count,
            "glossary": glossary.get(pid, 0),
        }
    return facts


def _estimate(facts: BookFacts, stage: str, done: int, total: int) -> dict:
    spent_cost = facts.spent_cost
    if stage not in STAGE_OPERATIONS or not done or done >= total:
        return {
            "remaining_seconds": 0 if done >= total else None,
            "remaining_cost": 0 if done >= total else None,
            "spent_cost": spent_cost,
            "confidence": "insufficient" if not done else "complete",
        }
    requests, duration, stage_cost = facts.stage_requests.get(stage, (0, 0, 0))
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


@dataclass
class ReviewState:
    targets: set[str]
    done_ids: set[str]
    outcomes: dict[str, dict]


def _review_states(db, project_ids: list[str]) -> dict[str, ReviewState]:
    """Final review targets and outcomes of every listed book, in one query."""
    states = {pid: ReviewState(set(), set(), {}) for pid in project_ids}
    # Newest job first: its outcome for a passage wins over an older review of the same passage.
    for pid, sid, step, outcome, data in db.execute(
        select(
            Job.project_id,
            JobSegmentState.segment_id,
            JobSegmentState.step,
            JobSegmentState.outcome,
            JobSegmentState.data,
        )
        .join(Job, Job.id == JobSegmentState.job_id)
        .where(
            Job.project_id.in_(project_ids),
            Job.operation.in_(REVIEW_STATE_OPERATIONS),
            JobSegmentState.step.in_(REVIEW_STEPS),
        )
        .order_by(Job.created_at.desc())
    ):
        review = states[pid]
        if step == state.REVIEW_TARGET:
            review.targets.add(sid)
            continue
        review.done_ids.add(sid)
        if outcome:
            review.outcomes.setdefault(sid, {**data, "outcome": outcome})
    for review in states.values():
        if review.targets:
            review.done_ids.intersection_update(review.targets)
    return states


def _progress(project: Project, facts: BookFacts, review: ReviewState, models: dict, statuses: dict) -> dict:
    stats = facts.stats
    active_job = next((job for job in facts.jobs if job.status in HELD), None)
    job = active_job or (facts.jobs[0] if facts.jobs else None)
    checkpoint = job.checkpoint if job else {}
    analysis_done = stats["analyzed_segments"] + stats["synthesized_chapters"]
    analysis_total = stats["total"] + stats["chapters"]
    translation_started = bool(stats["translated"] or stats["errors"] or stats["refused"])
    review_done = len(review.done_ids) or stats["reviewed_segments"]
    review_total = len(review.targets) or stats["review_total"]
    active_review = bool(
        active_job
        and (
            checkpoint.get("step") in {"final_review", "critique_acceptance"}
            or active_job.operation in ACTIVE_REVIEW_OPERATIONS
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
    if active_job and (step == "final_review" or active_job.operation in ACTIVE_REVIEW_OPERATIONS):
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
    outcome_for = {
        sid: review.outcomes.get(sid, {}).get("outcome")
        or (
            "protected"
            if sid in statuses and (statuses[sid][1] or statuses[sid][2])
            else "resolved"
            if sid in statuses and statuses[sid][0] == "ok"
            else "needs_human"
        )
        for sid in review.done_ids
    }
    review_summary = {
        "examined": len(review.done_ids),
        "total": review_total,
        "resolved": sum(value == "resolved" for value in outcome_for.values()),
        "needs_human": sum(value == "needs_human" for value in outcome_for.values()),
        "remaining": stats["flagged"],
        "protected": sum(value == "protected" for value in outcome_for.values()),
        "revised": sum(1 for value in review.outcomes.values() if value.get("revised")),
        "failed": sum(value == "failed" for value in outcome_for.values()),
    }
    return {
        "active_stage": active,
        "state": job.status if job else project.status,
        "operation": job.operation if job else None,
        "job_id": job.id if job else None,
        "next_attempt": job.next_attempt if job else 0,
        "stop_reason": job.stop_reason if job else "",
        "model": models.get(provider_id) if provider_id else None,
        "current": current,
        "stages": stages,
        "review": review_summary,
        "estimate": _estimate(facts, active, current["done"], current["total"]),
    }


def books_progress(db, projects: list[Project], facts: dict[str, BookFacts]) -> dict[str, dict]:
    reviews = _review_states(db, [project.id for project in projects]) if projects else {}
    # Only a reviewed passage without a recorded outcome needs its current state.
    unknown = {
        sid
        for review in reviews.values()
        for sid in review.done_ids
        if not review.outcomes.get(sid, {}).get("outcome")
    }
    statuses = (
        {
            sid: (status, human, validated)
            for sid, status, human, validated in db.execute(
                select(Segment.id, Segment.status, Segment.human, Segment.validated).where(
                    Segment.project_id.in_([project.id for project in projects]), Segment.id.in_(unknown)
                )
            )
        }
        if unknown
        else {}
    )
    models = dict(db.execute(select(Provider.id, Provider.model)).all())
    return {
        project.id: _progress(project, facts[project.id], reviews[project.id], models, statuses)
        for project in projects
    }


def project_stats(db, project: Project) -> dict:
    return book_facts(db, [project.id])[project.id].stats


def project_progress(db, project: Project) -> dict:
    facts = book_facts(db, [project.id])
    return books_progress(db, [project], facts)[project.id]
