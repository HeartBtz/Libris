import time

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.common import Identified

SERIAL = "project_kind = 'serial'"


class Project(Identified, Base):
    """A volume of a series, the chapter container of a webnovel, or a standalone book (no series)."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("series_id", "external_id", name="uq_projects_series_external"),
        # A series has at most one continuous container for chapters imported without a volume.
        Index(
            "uq_projects_series_serial",
            "series_id",
            unique=True,
            postgresql_where=text(SERIAL),
            sqlite_where=text(SERIAL),
        ),
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(500), default="")
    series_id: Mapped[str | None] = mapped_column(ForeignKey("series.id"), index=True)
    # Orders the unit of work (a series created in the same flush is inserted first).
    series = relationship("Series", foreign_keys=[series_id], lazy="select")
    # Kept in step with the series name for clients of the 0.5 API; `series_id` is authoritative.
    series_name: Mapped[str] = mapped_column(String(500), default="", server_default="", index=True)
    volume_number: Mapped[int | None] = mapped_column(Integer)
    source_format: Mapped[str] = mapped_column(String(10), default="epub", server_default="epub")
    project_kind: Mapped[str] = mapped_column(String(10), default="volume", server_default="volume")
    external_id: Mapped[str | None] = mapped_column(String(200))
    # {"version": 1, "adapter": ..., "files": [...], ...}: how the volume was imported.
    import_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    archived_at: Mapped[float | None] = mapped_column(Float, index=True)
    source_language: Mapped[str] = mapped_column(String(80), default="en")
    target_language: Mapped[str] = mapped_column(String(80), default="fr")
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"))
    quality: Mapped[str] = mapped_column(String(30), default="normal")
    context_backend: Mapped[str] = mapped_column(String(20), default="internal")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    # EPUB volumes only (empty for TXT and JSON sources); the source files are `SourceAsset` rows.
    original_hash: Mapped[str] = mapped_column(String(64), default="")
    original_path: Mapped[str] = mapped_column(Text, default="")
    book_info: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    instructions: Mapped[str] = mapped_column(Text, default="")
    bible: Mapped[dict] = mapped_column(JSON, default=dict)
    bible_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class Membership(Base):
    __tablename__ = "memberships"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="reader")


class Chapter(Identified, Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("project_id", "external_id", name="uq_chapters_project_external"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    # EPUB: the document inside the book. TXT/JSON: a virtual, stable name derived from the source.
    resource: Mapped[str] = mapped_column(Text)
    source_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_assets.id", ondelete="SET NULL"), index=True
    )
    external_id: Mapped[str | None] = mapped_column(String(200))
    # The number the author gave the chapter; `position` is only the order inside the volume.
    chapter_number: Mapped[float | None] = mapped_column(Float)
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    import_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # An earlier chapter's source was replaced: this one's analysis may rest on outdated context.
    context_stale: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # narrative | auxiliary (linear="no") | navigation (nav, NCX) | metadata (OPF description)
    kind: Mapped[str] = mapped_column(String(20), default="narrative", server_default="narrative")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    instructions: Mapped[str] = mapped_column(Text, default="")
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False)


class Segment(Identified, Base):
    __tablename__ = "segments"
    __table_args__ = (UniqueConstraint("project_id", "position"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(Text)
    # Hash of the normalized source units: finds identical passages across the owner's books.
    source_key: Mapped[str | None] = mapped_column(String(64), index=True)
    units: Mapped[list] = mapped_column(JSON)
    translation: Mapped[str] = mapped_column(Text, default="")
    translated_units: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    stage: Mapped[str] = mapped_column(String(30), default="pending")
    human: Mapped[bool] = mapped_column(Boolean, default=False)
    retained_source: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    instructions: Mapped[str] = mapped_column(Text, default="")
    uncertainties: Mapped[list] = mapped_column(JSON, default=list)
    critique: Mapped[list] = mapped_column(JSON, default=list)
    narrative: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class TranslationVersion(Identified, Base):
    __tablename__ = "translation_versions"
    segment_id: Mapped[str] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"), index=True)
    units: Mapped[list] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(30))
    base_revision: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
