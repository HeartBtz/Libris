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
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
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


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[float] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSON)


class RequestLog(Identified, Base):
    __tablename__ = "llm_requests"
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
    error: Mapped[str] = mapped_column(Text, default="")


class Issue(Identified, Base):
    __tablename__ = "quality_issues"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(50))
    message: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
