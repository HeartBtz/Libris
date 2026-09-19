"""Removals of OpenViking documents left behind by a deleted volume or series (`openviking_cleanups`).

A row is queued in the same transaction as the SQL deletion when the cleanup is switched on (see
app.engines.memory.cleanup), or by an administrator for orphans found by a dry run. The worker
removes its directories later: nothing here refers to the deleted rows, so the log outlives them.
"""

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified


class OpenVikingCleanup(Identified, Base):
    __tablename__ = "openviking_cleanups"
    kind: Mapped[str] = mapped_column(String(20))  # volume | series | orphans
    # Not foreign keys: the owner's volume or series no longer exists when the row is worked on.
    owner_id: Mapped[str] = mapped_column(String(36), default="")
    target_id: Mapped[str] = mapped_column(String(36), default="")
    label: Mapped[str] = mapped_column(String(500), default="")
    requested_by: Mapped[str] = mapped_column(String(36), default="")
    root_uri: Mapped[str] = mapped_column(Text)
    uris: Mapped[list] = mapped_column(JSON, default=list)
    # One entry per directory handled: {"uri", "status": removed|absent|kept, "reason", "documents"...}.
    results: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|running|done
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[float | None] = mapped_column(Float)
