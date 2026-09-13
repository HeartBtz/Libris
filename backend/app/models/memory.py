from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified


class BibleRevision(Identified, Base):
    __tablename__ = "bible_revisions"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    content: Mapped[dict] = mapped_column(JSON)
    human: Mapped[bool] = mapped_column(Boolean, default=False)


class Entity(Identified, Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("project_id", "name", "category"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_validated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"))


class EntityMerge(Identified, Base):
    __tablename__ = "entity_merges"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    source_ids: Mapped[list] = mapped_column(JSON)
    snapshots: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    human: Mapped[bool] = mapped_column(Boolean, default=False)


class CharacterRelation(Identified, Base):
    __tablename__ = "character_relations"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id", ondelete="SET NULL"))
    position: Mapped[int] = mapped_column(Integer, default=-1)
    relation_type: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str] = mapped_column(String(30), default="analysis")
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Glossary(Identified, Base):
    __tablename__ = "glossary"
    __table_args__ = (UniqueConstraint("project_id", "source"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(300))
    translation: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(50), default="autre")
    description: Mapped[str] = mapped_column(Text, default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)


class Memory(Identified, Base):
    __tablename__ = "memories"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=-1)
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict] = mapped_column(JSON)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)


class Outbox(Identified, Base):
    __tablename__ = "memory_outbox"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(150), unique=True)
    session_name: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str] = mapped_column(Text, default="")


class Prompt(Identified, Base):
    __tablename__ = "prompts"
    name: Mapped[str] = mapped_column(String(80), index=True)
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
