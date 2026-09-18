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
