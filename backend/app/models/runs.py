from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified


class Job(Identified, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_live_job_project",
            "project_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending','waiting','paused','blocked','analyzing','translating','reviewing','syncing')"
            ),
            sqlite_where=text(
                "status IN ('pending','waiting','paused','blocked','analyzing','translating','reviewing','syncing')"
            ),
        ),
        Index("ix_jobs_provider_status", "provider_id", "status"),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[str | None] = mapped_column(
        ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_owner: Mapped[str] = mapped_column(String(36), default="")
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    next_attempt: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    outage_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    stop_reason: Mapped[str] = mapped_column(String(40), default="", server_default="")
    # When the job last became completed, failed or cancelled; bounds the retention of its state.
    finished_at: Mapped[float | None] = mapped_column(Float)
    # What the job produced, read by clients once it is terminal (e.g. {"autopilot": {...}}).
    result: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # Fair queue (app.jobs.fairness): 0 low, 1 normal, 2 high; the API token that asked for the job;
    # when it last entered the queue (launch, resume) and when a worker last picked it.
    priority: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    token_id: Mapped[str | None] = mapped_column(
        ForeignKey("api_tokens.id", ondelete="SET NULL", name="fk_jobs_token_id"), nullable=True
    )
    queued_at: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    claimed_at: Mapped[float | None] = mapped_column(Float)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_project_id_id", "project_id", "id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class RequestLog(Identified, Base):
    __tablename__ = "llm_requests"
    __table_args__ = (
        # Admission control counts the running calls of a provider before every model call.
        Index(
            "ix_llm_requests_running",
            "provider_id",
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    execution_owner: Mapped[str] = mapped_column(String(36), default="", server_default="")
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"))
    operation: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    parameters: Mapped[dict] = mapped_column(JSON)
    messages: Mapped[list] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    duration: Mapped[float] = mapped_column(Float, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    # Price per million tokens when the call was made; NULL on rows that predate the columns.
    input_cost: Mapped[float | None] = mapped_column(Float)
    output_cost: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str] = mapped_column(Text, default="")


class Issue(Identified, Base):
    __tablename__ = "quality_issues"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class JobSegmentState(Base):
    """What a job has settled, passage by passage or batch by batch.

    Kept out of `jobs.checkpoint`, which only holds a cursor and counters: a list per passage there was
    rewritten on every step and sent back with every job listing, whatever the size of the book.
    """

    __tablename__ = "job_segment_state"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    step: Mapped[str] = mapped_column(String(30), primary_key=True)
    # Empty for book-level batches (bible consolidation, consistency samples).
    segment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default="")
    key: Mapped[str] = mapped_column(String(100), primary_key=True, default="")
    outcome: Mapped[str] = mapped_column(String(30), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AutopilotDecision(Identified, Base):
    """One decision the autopilot took instead of a person, with its reason: none is silent."""

    __tablename__ = "autopilot_decisions"
    __table_args__ = (Index("ix_autopilot_decisions_project_created", "project_id", "created_at"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    # Kept when the passage is re-imported: the log outlives what it talks about.
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id", ondelete="SET NULL"), index=True)
    stage: Mapped[str] = mapped_column(String(40))  # analysis, translation, recovery, arbitration, memory…
    kind: Mapped[str] = mapped_column(String(40))  # what was decided on: critique, glossary_term, outage…
    action: Mapped[str] = mapped_column(String(40))  # what was done: applied, rejected, source_retained…
    reason: Mapped[str] = mapped_column(Text, default="")
    # Name and model of the provider involved, as they were then (a provider can be renamed or deleted).
    provider: Mapped[str] = mapped_column(String(200), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
