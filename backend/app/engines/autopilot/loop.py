"""The convergence loop that replaces every human step after the translation.

Each round: recovery ladder for failed passages → (first round, high/maximum quality) book consistency →
final review → AI arbitration of what is still open. The loop stops when nothing is failed or open
(stable) or after AUTOPILOT_MAX_ROUNDS; then the leftovers are settled — open points closed on the current
translation, failed passages kept in the original with their reason — and the report is written to
`job.result["autopilot"]`. The round and phase are checkpointed: a resumed job continues where it was.
"""

from sqlalchemy import delete, func, select

from app.automation_settings import autopilot_config
from app.config import settings
from app.db import SessionLocal
from app.engines.autopilot.arbitration import arbitrate, open_condition, settle_open
from app.engines.autopilot.decisions import record
from app.engines.autopilot.memory import decide_glossary, decide_memory
from app.engines.autopilot.recovery import failed_condition, recover_failed, retain_source
from app.engines.quality import score as quality
from app.jobs import segment_state as state
from app.jobs.concurrency import blocking, job_lock
from app.jobs.queue import checkpoint, emit, fence
from app.models import Issue, Job, JobSegmentState, Project, Segment

PHASES = ("recovery", "consistency", "review", "arbitration", "done")


def _start_round(job: Job, owner: str, round_no: int) -> Job:
    """A new round reviews its own passages: the previous round's final review state is dropped."""
    with job_lock(job.id), SessionLocal() as db:
        current = fence(db, job.id, owner)
        if round_no > 1:
            db.execute(
                delete(JobSegmentState).where(
                    JobSegmentState.job_id == job.id,
                    JobSegmentState.step.in_((state.REVIEW_TARGET, state.REVIEWED)),
                )
            )
        progress = {key: value for key, value in current.checkpoint.items() if key != "review_targets"}
        current.checkpoint = {
            **progress,
            "step": "autopilot",
            "autopilot_round": round_no,
            "autopilot_phase": PHASES[0],
            "segment_id": None,
        }
        emit(
            db,
            job.project_id,
            job_id=job.id,
            status=current.status,
            step="autopilot",
            autopilot_round=round_no,
        )
        db.commit()
        return current


def _phase(job: Job, owner: str, phase: str) -> Job:
    return checkpoint(job.id, owner, {"step": "autopilot", "autopilot_phase": phase, "segment_id": None})


def _open_counts(job: Job) -> tuple[int, int]:
    with SessionLocal() as db:
        failed = db.scalar(select(func.count(Segment.id)).where(*failed_condition(job.project_id)))
        open_ = db.scalar(select(func.count(Segment.id)).where(*open_condition(job.project_id)))
        return failed, open_


def _quality(project_id: str) -> str:
    with SessionLocal() as db:
        return db.get(Project, project_id).quality


def round_targets(job: Job, round_no: int):
    """Later rounds review what is still open and what the ladder recovered in this round."""

    def select_targets(db) -> list[str]:
        recovered = select(JobSegmentState.segment_id).where(
            JobSegmentState.job_id == job.id,
            JobSegmentState.step == state.LADDER,
            JobSegmentState.key == f"r{round_no}",
            JobSegmentState.outcome == "recovered",
        )
        open_ids = select(Segment.id).where(*open_condition(job.project_id))
        return list(
            db.scalars(
                select(Segment.id)
                .where(
                    Segment.project_id == job.project_id,
                    Segment.human.is_(False),
                    Segment.retained_source.is_(False),
                    Segment.id.in_(open_ids) | Segment.id.in_(recovered),
                )
                .order_by(Segment.position)
            )
        )

    return select_targets


async def converge(job: Job, owner: str) -> None:
    from app.engines.translation.final_review import resolve_validations
    from app.engines.translation.pipeline import consistency

    job = await blocking(checkpoint, job.id, owner)
    limit = autopilot_config()["max_rounds"]
    round_no = int(job.checkpoint.get("autopilot_round") or 0)
    if not round_no:
        round_no = 1
        # Terms proposed while translating are decided before the review looks at the book.
        await blocking(decide_glossary, job, owner)
        job = await blocking(_start_round, job, owner, round_no)
    while True:
        phase = job.checkpoint.get("autopilot_phase", PHASES[0])
        if phase == "recovery":
            await recover_failed(job, owner, round_no)
            job, phase = await blocking(_phase, job, owner, "consistency"), "consistency"
        if phase == "consistency":
            if round_no == 1 and await blocking(_quality, job.project_id) in {"high", "maximum"}:
                await consistency(job, owner)
            job, phase = await blocking(_phase, job, owner, "review"), "review"
        if phase == "review":
            if settings().final_review_enabled and job.options.get("final_review", True):
                await resolve_validations(job, owner, None if round_no == 1 else round_targets(job, round_no))
            job, phase = await blocking(_phase, job, owner, "arbitration"), "arbitration"
        if phase == "arbitration":
            await arbitrate(job, owner, round_no)
            job = await blocking(_phase, job, owner, "done")
        failed, open_ = await blocking(_open_counts, job)
        stable = not failed and not open_
        await blocking(_note_round, job, owner, round_no, failed, open_, stable)
        if stable or round_no >= limit:
            break
        round_no += 1
        job = await blocking(_start_round, job, owner, round_no)
    await blocking(settle, job, owner, round_no, stable)


def _note_round(job: Job, owner: str, round_no: int, failed: int, open_: int, stable: bool) -> None:
    with SessionLocal() as db:
        current = fence(db, job.id, owner)
        record(
            db,
            job.project_id,
            job_id=job.id,
            stage="convergence",
            kind="round",
            action="stable" if stable else "continue",
            reason=f"Tour {round_no} : {failed} passage(s) en échec, {open_} avec des points ouverts.",
        )
        current.checkpoint = {**current.checkpoint, "autopilot_rounds_done": round_no}
        db.commit()


def residuals(db, project_id: str) -> list[dict]:
    rows = db.execute(
        select(Segment.id, Segment.chapter_id)
        .where(Segment.project_id == project_id, Segment.retained_source.is_(True))
        .order_by(Segment.position)
    ).all()
    reasons = dict(
        db.execute(
            select(Issue.segment_id, Issue.message)
            .where(Issue.project_id == project_id, Issue.code == "source_retained", Issue.resolved.is_(False))
            .order_by(Issue.created_at)
        ).all()
    )
    return [{"segment_id": sid, "chapter_id": cid, "reason": reasons.get(sid, "")} for sid, cid in rows]


def quality_summary(db, project_id: str) -> dict:
    """Passage scores once the run is settled (see app.engines.quality.score)."""
    quality.repair(db, [project_id])
    return quality.summary(db, [project_id])


def settle(job: Job, owner: str, rounds: int, stable: bool) -> dict:
    """Terminal: nothing is left failed or waiting for a decision, and the report says what remains."""
    why = "Stable." if stable else f"Nombre maximal de tours atteint ({rounds})."
    with SessionLocal() as db:
        leftovers = list(db.scalars(select(Segment.id).where(*failed_condition(job.project_id))))
    for sid in leftovers:
        retain_source(job, owner, sid, f"{why} Aucune traduction valide obtenue.")
    settle_open(job, owner, why)
    decide_memory(job, owner, translated=True)
    with job_lock(job.id), SessionLocal() as db:
        current = fence(db, job.id, owner)
        remaining = residuals(db, job.project_id)
        outcome = "completed_with_residuals" if remaining else "completed"
        report = {
            "outcome": outcome,
            "rounds": rounds,
            "residuals": remaining,
            "reason": None
            if not remaining
            else f"{len(remaining)} passage(s) conservé(s) dans la langue d’origine faute de traduction valide.",
            "quality": quality_summary(db, job.project_id),
        }
        current.result = {**(current.result or {}), "autopilot": report}
        record(
            db,
            job.project_id,
            job_id=job.id,
            stage="report",
            kind="job",
            action=outcome,
            reason=f"{why} {rounds} tour(s) ; {len(remaining)} passage(s) conservé(s) dans l’original.",
        )
        emit(
            db, job.project_id, job_id=job.id, status=current.status, step="autopilot_report", outcome=outcome
        )
        db.commit()
        return report
