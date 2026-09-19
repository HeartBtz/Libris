"""Daily usage aggregates of model requests (`usage_daily`), kept after the requests themselves.

One row per day (UTC), book, provider, operation, model, outcome and cache flag, with the token
counts, time and cost of its requests at the price recorded with each request. Rows are added by
`app.maintenance.usage.rollup`; the statistics read them plus the few requests not rolled up yet.
"""

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.runs import RequestLog


class UsageDaily(Base):
    __tablename__ = "usage_daily"
    __table_args__ = (
        UniqueConstraint(
            "day",
            "project_id",
            "provider_id",
            "operation",
            "model",
            "status",
            "cached",
            name="uq_usage_daily_key",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD, UTC
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # Not a foreign key: a deleted provider keeps its history ("" when the request had none).
    provider_id: Mapped[str] = mapped_column(String(36), default="")
    operation: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30))
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    completion_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)


# The statistics read the requests not rolled up yet, the most recent ones, by creation time.
Index("ix_llm_requests_created_at", RequestLog.created_at)
