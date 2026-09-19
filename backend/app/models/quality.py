"""Quality score of each passage (0–100), kept in step with the passage and what is known about it.

The score is derived from signals Libris already records (see `app.engines.quality.score`): it is
recomputed at the commit of any transaction that changes a passage, its quality alerts, the autopilot's
decisions about it or the failed model calls it cost. Changes made through the ORM are seen in
`after_flush`; bulk UPDATE/DELETE statements on passages and alerts in `do_orm_execute`.
"""

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, event, inspect, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.models.books import Segment
from app.models.runs import AutopilotDecision, Issue, RequestLog

PENDING = "libris_quality_pending"
# What a passage's score reads; a change to anything else (position, stage, instructions) keeps it.
SCORED_ATTRIBUTES = (
    "source",
    "translation",
    "translated_units",
    "status",
    "retained_source",
    "validated",
    "critique",
    "uncertainties",
    "error",
)
FAILED_CALLS = ("error", "refused", "interrupted", "abandoned")


class PassageQuality(Base):
    """One row per passage that has something to score (a translation or its retained original)."""

    __tablename__ = "passage_quality"
    __table_args__ = (Index("ix_passage_quality_project_score", "project_id", "score"),)
    segment_id: Mapped[str] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(10))  # good | fair | weak | poor
    # [{"code", "count", "penalty"}], largest penalty first: why the score is below 100.
    signals: Mapped[list] = mapped_column(JSON, default=list)
    # The passage revision the score was computed on (a lazily repaired row is recognised by it).
    revision: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[float] = mapped_column(Float)


def _pending(session: Session) -> set[str]:
    return session.info.setdefault(PENDING, set())


@event.listens_for(Session, "after_flush")
def collect_changes(session: Session, _context) -> None:
    # After the flush: new rows have their ids; `new`, `dirty` and the attribute history still describe it.
    pending = _pending(session)
    for obj in session.new:
        if isinstance(obj, Segment):
            # A new passage has nothing to score until it is translated (or restored from an archive).
            if obj.translation or obj.retained_source:
                pending.add(obj.id)
        elif isinstance(obj, (Issue, AutopilotDecision)) and obj.segment_id:
            pending.add(obj.segment_id)
        elif isinstance(obj, RequestLog) and obj.segment_id and obj.status in FAILED_CALLS:
            pending.add(obj.segment_id)
    for obj in session.dirty:
        if isinstance(obj, Segment):
            state = inspect(obj)
            if any(state.attrs[name].history.has_changes() for name in SCORED_ATTRIBUTES):
                pending.add(obj.id)
        elif isinstance(obj, Issue) and obj.segment_id:
            pending.add(obj.segment_id)
        elif isinstance(obj, RequestLog) and obj.segment_id and obj.status in FAILED_CALLS:
            if inspect(obj).attrs.status.history.has_changes():
                pending.add(obj.segment_id)
    for obj in session.deleted:
        if isinstance(obj, Issue) and obj.segment_id:
            pending.add(obj.segment_id)


@event.listens_for(Session, "do_orm_execute")
def collect_bulk_changes(state) -> None:
    if not (state.is_update or state.is_delete):
        return
    statement = state.statement
    table = getattr(getattr(statement, "table", None), "name", None)
    if table == Segment.__tablename__:
        column = Segment.id
    elif table == Issue.__tablename__:
        column = Issue.segment_id
    else:
        return
    where = statement.whereclause
    if where is None:
        return  # never used on these tables: nothing to name
    # The rows are named before the statement runs: a DELETE leaves nothing to look up afterwards.
    ids = state.session.scalars(select(column).where(where)).all()
    _pending(state.session).update(sid for sid in ids if sid)


@event.listens_for(Session, "before_commit")
def refresh_scores(session: Session) -> None:
    if not session.info.get(PENDING) and not (session.new or session.dirty or session.deleted):
        return
    from app.engines.quality.score import refresh

    session.flush()  # the last changes are collected before the scores are computed
    for _ in range(3):
        ids = session.info.pop(PENDING, None)
        if not ids:
            return
        refresh(session, ids)
