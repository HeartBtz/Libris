"""Shared glossaries: one named terminology for several series of the same universe.

A series attaches to at most one shared glossary. Its terms come last in precedence: the book, then
the series (its glossary and earlier volumes), then the shared glossary (app.engines.context.series).
"""

import time

from sqlalchemy import Boolean, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified


class SharedGlossary(Identified, Base):
    __tablename__ = "shared_glossaries"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_name", name="uq_shared_glossary_owner_name"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Case and spacing folded: "Universe" and " universe " name the same glossary of one owner.
    normalized_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Optional: a glossary with languages only applies to volumes of the same language pair.
    source_language: Mapped[str | None] = mapped_column(String(80))
    target_language: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class SharedTerm(Identified, Base):
    __tablename__ = "shared_glossary_terms"
    __table_args__ = (UniqueConstraint("glossary_id", "source", name="uq_shared_term_source"),)
    glossary_id: Mapped[str] = mapped_column(
        ForeignKey("shared_glossaries.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(300))
    translation: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(50), default="autre")
    description: Mapped[str] = mapped_column(Text, default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class SeriesSharedGlossary(Base):
    """The shared glossary a series follows (one per series)."""

    __tablename__ = "series_shared_glossaries"
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), primary_key=True)
    glossary_id: Mapped[str] = mapped_column(
        ForeignKey("shared_glossaries.id", ondelete="CASCADE"), index=True
    )
    attached_at: Mapped[float] = mapped_column(Float, default=time.time)
