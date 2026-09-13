import time

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified


class Project(Identified, Base):
    __tablename__ = "projects"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(500), default="")
    series_name: Mapped[str] = mapped_column(String(500), default="", server_default="", index=True)
    volume_number: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[float | None] = mapped_column(Float, index=True)
    source_language: Mapped[str] = mapped_column(String(80), default="en")
    target_language: Mapped[str] = mapped_column(String(80), default="fr")
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"))
    quality: Mapped[str] = mapped_column(String(30), default="normal")
    context_backend: Mapped[str] = mapped_column(String(20), default="internal")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    original_hash: Mapped[str] = mapped_column(String(64))
    original_path: Mapped[str] = mapped_column(Text)
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
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    resource: Mapped[str] = mapped_column(Text)
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
