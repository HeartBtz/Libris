"""What a job has settled per passage or per batch, stored as rows (see `JobSegmentState`)."""

from collections.abc import Iterable

from sqlalchemy import Select, delete, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.models import JobSegmentState, Segment

FINISHED = "finished"  # nothing left to do for this passage in this job
STARTED = "started"  # a forced rerun already applied its new translation
REVIEW_TARGET = "review_target"  # passages the final review set out to examine
REVIEWED = "reviewed"  # final review done; outcome: resolved, needs_human, protected or failed
RECOVERY_TARGET = "recovery_target"  # passages retried by the automatic recovery pass
REPAIR = "repair"  # one validated four-unit batch of a repaired passage
CONSISTENCY = "consistency"  # one global consistency sample already checked
BIBLE = "bible"  # one batch of chapter evidence already consolidated into the bible
ANALYSIS_SKIPPED = "analysis_skipped"  # autopilot: analysis given up for this passage, the book goes on
LADDER = "autopilot_ladder"  # autopilot: failed passage taken through the recovery ladder (key: round)
ARBITRATED = "autopilot_arbitrated"  # autopilot: open proposals of the passage decided (key: round)

KEYS = ("job_id", "step", "segment_id", "key")
CHUNK = 500


def _insert(db: Session):
    dialect = postgresql if db.get_bind().dialect.name == "postgresql" else sqlite
    return dialect.insert(JobSegmentState)


def mark(
    db: Session,
    job_id: str,
    step: str,
    segment_id: str = "",
    *,
    key: str = "",
    outcome: str = "",
    data: dict | None = None,
) -> None:
    """Idempotent: the API and a resumed job may record the same passage twice."""
    values = {"outcome": outcome, "data": data or {}}
    statement = _insert(db).values(job_id=job_id, step=step, segment_id=segment_id, key=key, **values)
    db.execute(statement.on_conflict_do_update(index_elements=KEYS, set_=values))


def mark_all(db: Session, job_id: str, step: str, segment_ids: Iterable[str]) -> None:
    rows = [
        {"job_id": job_id, "step": step, "segment_id": sid, "key": "", "outcome": "", "data": {}}
        for sid in dict.fromkeys(segment_ids)
    ]
    for start in range(0, len(rows), CHUNK):
        db.execute(_insert(db).values(rows[start : start + CHUNK]).on_conflict_do_nothing(index_elements=KEYS))


def segments(job_id: str, step: str) -> Select:
    """For `Segment.id.in_(...)`: the database filters, rather than a list of every id sent back."""
    return select(JobSegmentState.segment_id).where(
        JobSegmentState.job_id == job_id, JobSegmentState.step == step
    )


def marked(db: Session, job_id: str, step: str) -> set[str]:
    return set(db.scalars(segments(job_id, step)))


def is_marked(db: Session, job_id: str, step: str, segment_id: str = "", key: str = "") -> bool:
    return bool(
        db.scalar(
            select(JobSegmentState.job_id).where(
                JobSegmentState.job_id == job_id,
                JobSegmentState.step == step,
                JobSegmentState.segment_id == segment_id,
                JobSegmentState.key == key,
            )
        )
    )


def batches(db: Session, job_id: str, step: str, segment_id: str = "") -> dict[str, dict]:
    return dict(
        db.execute(
            select(JobSegmentState.key, JobSegmentState.data).where(
                JobSegmentState.job_id == job_id,
                JobSegmentState.step == step,
                JobSegmentState.segment_id == segment_id,
            )
        ).all()
    )


def in_book_order(db: Session, job_id: str, step: str) -> list[str]:
    return list(
        db.scalars(
            select(JobSegmentState.segment_id)
            .join(Segment, Segment.id == JobSegmentState.segment_id)
            .where(JobSegmentState.job_id == job_id, JobSegmentState.step == step)
            .order_by(Segment.position)
        )
    )


def forget(db: Session, job_id: str, steps: tuple[str, ...], segment_ids: list[str]) -> None:
    for start in range(0, len(segment_ids), CHUNK):
        db.execute(
            delete(JobSegmentState).where(
                JobSegmentState.job_id == job_id,
                JobSegmentState.step.in_(steps),
                JobSegmentState.segment_id.in_(segment_ids[start : start + CHUNK]),
            )
        )


_LEGACY_LISTS = {
    "finished_ids": FINISHED,
    "started_ids": STARTED,
    "final_review_targets": REVIEW_TARGET,
    "automatic_recovery_targets": RECOVERY_TARGET,
}
_LEGACY_BATCHES = {"consistency_batches": CONSISTENCY, "analysis_batches": BIBLE}
_LEGACY = (
    *_LEGACY_LISTS, *_LEGACY_BATCHES, "final_review_done", "final_review_outcomes", "repair", "repair_progress"
)


def split_legacy(checkpoint: dict) -> tuple[dict, list[dict]]:
    """A checkpoint written before v0.5 (lists per passage), as a compact checkpoint and state rows.

    Same conversion as migration b856c2e068f8, for project archives exported before it.
    """
    rows: dict[tuple, dict] = {}

    def add(step, segment_id="", key="", outcome="", data=None):
        rows[(step, segment_id, key)] = {
            "step": step, "segment_id": segment_id, "key": key, "outcome": outcome, "data": data or {}
        }

    for name, step in _LEGACY_LISTS.items():
        for segment_id in checkpoint.get(name) or []:
            add(step, segment_id)
    outcomes = checkpoint.get("final_review_outcomes") or {}
    for segment_id in [*(checkpoint.get("final_review_done") or []), *outcomes]:
        data = dict(outcomes.get(segment_id) or {})
        add(REVIEWED, segment_id, outcome=str(data.pop("outcome", ""))[:30], data=data)
    for name, parts in (checkpoint.get("repair") or {}).items():
        segment_id, _, rest = name.partition(":")
        for start, data in parts.items():
            add(REPAIR, segment_id, f"{rest}:{start}", data=data)
    for name, step in _LEGACY_BATCHES.items():
        for key in checkpoint.get(name) or []:
            add(step, key=key)
    compact = {key: value for key, value in checkpoint.items() if key not in _LEGACY}
    if "final_review_targets" in checkpoint:
        compact["review_targets"] = len(checkpoint["final_review_targets"] or [])
    return compact, list(rows.values())
