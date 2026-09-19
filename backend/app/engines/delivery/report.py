"""Completion report of an automation request: what was translated, what kept its source and why,
what it cost and how long it took. Counted by the database; the residual list is bounded."""

import time

from sqlalchemy import case, func, literal_column, select

from app.models import AutopilotDecision, Chapter, Job, Project, RequestLog, Segment, TranslationRequest

REPORT_VERSION = 1
RESIDUAL_LIMIT = 500
FLAGGED = ("check", "error", "refused")
# A literal, not a bound parameter: PostgreSQL must see the same expression in SELECT and GROUP BY.
TRANSLATED = Segment.translation != literal_column("''")


def scope(request: TranslationRequest, project: Project):
    """The passages a request covers: its chapters, or the whole volume for an EPUB."""
    if request.options.get("input") == "epub":
        return Segment.project_id == project.id
    return Segment.chapter_id.in_(list(request.chapter_ids or []))


def autopilot_report(job: Job | None) -> dict | None:
    """What the autopilot (#63) left on the job, when it ran; the delivery works without it."""
    result = getattr(job, "result", None) if job is not None else None
    value = result.get("autopilot") if isinstance(result, dict) else None
    return value if isinstance(value, dict) else None


def decisions_count(db, job: Job | None) -> int | None:
    """Automatic decisions the autopilot (#63) logged for this job."""
    if job is None:
        return None
    return db.scalar(select(func.count()).select_from(AutopilotDecision).where(AutopilotDecision.job_id == job.id))


def residual_reason(retained: bool, error: str, autopilot: dict) -> str:
    """The autopilot's reason first, then the passage's own error, then what state it was left in."""
    return str(autopilot.get("reason") or error or ("source_retained" if retained else "untranslated"))


def usage(db, job: Job | None) -> dict:
    if job is None:
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_calls": 0, "cost": None}
    calls, prompt, completion, cached, priced, cost = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
            func.coalesce(func.sum(RequestLog.completion_tokens), 0),
            func.coalesce(func.sum(case((RequestLog.cached.is_(True), 1), else_=0)), 0),
            func.count(RequestLog.input_cost),
            func.coalesce(
                func.sum(
                    (RequestLog.prompt_tokens * func.coalesce(RequestLog.input_cost, 0)
                     + RequestLog.completion_tokens * func.coalesce(RequestLog.output_cost, 0)) / 1_000_000.0
                ),
                0,
            ),
        ).where(RequestLog.job_id == job.id)
    ).one()  # fmt: skip
    return {
        "calls": calls,
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "cached_calls": int(cached),
        # Only calls made with a known price are counted; None when none had one.
        "cost": round(float(cost), 6) if priced else None,
    }


def build_report(
    db, request: TranslationRequest, project: Project | None, job: Job | None, outcome: str, reason: str = "",
    delivery: dict | None = None, now: float | None = None,
) -> dict:  # fmt: skip
    now = now or time.time()
    autopilot = autopilot_report(job)
    passages = {"total": 0, "translated": 0, "source_retained": 0, "untranslated": 0, "flagged": 0,
                "validated": 0, "human": 0, "by_status": {}}  # fmt: skip
    residuals: list[dict] = []
    residual_total = 0
    if project is not None:
        where = scope(request, project)
        for status, translated, retained, validated, human, count in db.execute(
            select(
                Segment.status, TRANSLATED, Segment.retained_source, Segment.validated, Segment.human,
                func.count(),
            ).where(where).group_by(
                Segment.status, TRANSLATED, Segment.retained_source, Segment.validated, Segment.human
            )
        ):  # fmt: skip
            passages["total"] += count
            passages["by_status"][status] = passages["by_status"].get(status, 0) + count
            if retained:
                passages["source_retained"] += count
            elif translated:
                passages["translated"] += count
            else:
                passages["untranslated"] += count
            passages["flagged"] += count if status in FLAGGED and not validated else 0
            passages["validated"] += count if validated else 0
            passages["human"] += count if human else 0
        reasons = {
            item.get("segment_id"): item
            for item in (autopilot or {}).get("residuals", [])
            if isinstance(item, dict)
        }
        residual = (Segment.translation == "") | Segment.retained_source.is_(True)
        residual_total = db.scalar(select(func.count()).select_from(Segment).where(where, residual)) or 0
        chapters = {}
        for sid, chapter_id, position, status, retained, error in db.execute(
            select(
                Segment.id, Segment.chapter_id, Segment.position, Segment.status, Segment.retained_source,
                Segment.error,
            ).where(where, residual).order_by(Segment.position).limit(RESIDUAL_LIMIT)
        ):  # fmt: skip
            if chapter_id not in chapters:
                chapter = db.get(Chapter, chapter_id)
                chapters[chapter_id] = chapter
            chapter = chapters[chapter_id]
            residuals.append(
                {
                    "segment_id": sid,
                    "chapter_id": chapter_id,
                    "chapter_external_id": chapter.external_id if chapter else None,
                    "chapter_title": chapter.title if chapter else "",
                    "position": position,
                    "status": status,
                    "kept": "source",
                    "reason": residual_reason(retained, error, reasons.get(sid, {})),
                }
            )
    for item in (delivery or {}).get("fallbacks", []):
        # Passages restored to their source only in the delivered EPUB (markup, EPUBCheck repair).
        if item["reason"] in {"markup_mismatch", "epubcheck_repair"}:
            residual_total += 1
            if len(residuals) < RESIDUAL_LIMIT:
                residuals.append(
                    {"segment_id": item["segment_id"], "kept": "source", "reason": item["reason"]}
                )
    started = job.created_at if job else None
    finished = (job.finished_at if job else None) or now
    return {
        "version": REPORT_VERSION,
        "outcome": outcome,
        "reason": reason or None,
        "passages": passages,
        "residual_total": residual_total,
        "residuals": residuals,
        "residuals_truncated": residual_total > len(residuals),
        "usage": usage(db, job),
        "durations": {
            "total_seconds": round(now - request.created_at, 3),
            "queued_seconds": round(started - request.created_at, 3) if started else None,
            "job_seconds": round(finished - started, 3) if started else None,
        },
        "autopilot": {key: autopilot.get(key) for key in ("outcome", "rounds", "reason")}
        if autopilot
        else None,
        "decisions": {
            "autopilot": decisions_count(db, job),
            "intake": list(request.options.get("decisions") or []),
        },
        "delivery": {key: value for key, value in (delivery or {}).items() if key != "fallbacks"} or None,
    }
