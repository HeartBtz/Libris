"""Series, source files, API tokens and automation requests.

A series is what the library shows first; a `Project` stays the unit of work of the pipeline (one
volume, or the continuous chapter container of a webnovel).
"""

import time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.common import Identified

SERIES_KINDS = ("books", "webnovel")
SOURCE_FORMATS = ("epub", "txt", "json")
PROJECT_KINDS = ("volume", "serial")


class Series(Identified, Base):
    __tablename__ = "series"
    __table_args__ = (UniqueConstraint("owner_id", "normalized_name", name="uq_series_owner_name"),)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    # Case and spacing folded: "Saga" and " saga " name the same series of one owner.
    normalized_name: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str] = mapped_column(String(20), default="books", server_default="books")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    source_language: Mapped[str | None] = mapped_column(String(80))
    target_language: Mapped[str | None] = mapped_column(String(80))
    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    quality: Mapped[str | None] = mapped_column(String(30))
    context_backend: Mapped[str | None] = mapped_column(String(20))
    instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Series Bible: derived from the volumes unless a person validated an edited version.
    bible: Mapped[dict] = mapped_column(JSON, default=dict)
    bible_validated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archived_at: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class SourceAsset(Identified, Base):
    """One imported source file (or JSON payload), stored under DATA_DIR at a path Libris chose."""

    __tablename__ = "source_assets"
    __table_args__ = (Index("ix_source_assets_sha256", "sha256"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(10))
    original_name: Mapped[str] = mapped_column(String(500), default="")
    media_type: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    # Relative to DATA_DIR for files Libris wrote; absolute only for books imported before 0.6.
    storage_path: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class SeriesEntity(Identified, Base):
    """Canonical identity of a character, place, organization or object across the volumes."""

    __tablename__ = "series_entities"
    __table_args__ = (UniqueConstraint("series_id", "category", "name", name="uq_series_entity_name"),)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(50))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    validated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # First appearance: what an earlier volume may know of this identity.
    first_project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    first_volume_number: Mapped[int | None] = mapped_column(Integer)
    first_position: Mapped[int] = mapped_column(Integer, default=-1, server_default="-1")
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("series_entities.id", ondelete="SET NULL"))
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class SeriesEntityLink(Identified, Base):
    """A volume's local entity attached to a series identity; ambiguous matches stay proposals."""

    __tablename__ = "series_entity_links"
    __table_args__ = (UniqueConstraint("entity_id", "series_entity_id", name="uq_series_entity_link"),)
    series_entity_id: Mapped[str] = mapped_column(
        ForeignKey("series_entities.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # linked | proposed | rejected
    status: Mapped[str] = mapped_column(String(20), default="linked")
    confidence: Mapped[float] = mapped_column(Float, default=1)
    reason: Mapped[str] = mapped_column(String(300), default="")
    human: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class SeriesRelation(Identified, Base):
    __tablename__ = "series_relations"
    __table_args__ = (
        UniqueConstraint("series_id", "source_id", "target_id", "relation_type", name="uq_series_relation"),
    )
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("series_entities.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("series_entities.id", ondelete="CASCADE"), index=True)
    relation_type: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    first_project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    first_volume_number: Mapped[int | None] = mapped_column(Integer)
    first_position: Mapped[int] = mapped_column(Integer, default=-1, server_default="-1")
    validated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class SeriesTerm(Identified, Base):
    """Series glossary. `origin` = human rows are never rewritten by the aggregation of volumes."""

    __tablename__ = "series_glossary"
    __table_args__ = (UniqueConstraint("series_id", "source", name="uq_series_term_source"),)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(300))
    translation: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(50), default="autre")
    description: Mapped[str] = mapped_column(Text, default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[str] = mapped_column(String(20), default="volume", server_default="volume")
    first_project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    first_volume_number: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)


class AuditEntry(Identified, Base):
    """Decisions that must stay traceable: identity merges and splits, glossary overrides, tokens."""

    __tablename__ = "audit_entries"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    series_id: Mapped[str | None] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class ApiToken(Identified, Base):
    __tablename__ = "api_tokens"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    # SHA-256 of the secret; the secret itself is shown once and never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[float | None] = mapped_column(Float)
    revoked_at: Mapped[float | None] = mapped_column(Float)
    last_used_at: Mapped[float | None] = mapped_column(Float)
    # Signs the webhooks of this token's requests (HMAC-SHA256), encrypted with SECRET_KEY; shown once.
    webhook_secret: Mapped[str | None] = mapped_column(Text)


LIVE_REQUEST = "status IN ('queued','running')"
PENDING_WEBHOOK = "webhook_state = 'pending'"


class TranslationRequest(Identified, Base):
    """One automation request: its content is in SQL before the 202 answer, its progress in jobs."""

    __tablename__ = "translation_requests"
    __table_args__ = (
        UniqueConstraint("owner_id", "external_id", name="uq_translation_request_external"),
        UniqueConstraint("owner_id", "idempotency_key", name="uq_translation_request_idempotency"),
        Index(
            "ix_translation_requests_live",
            "status",
            postgresql_where=text(LIVE_REQUEST),
            sqlite_where=text(LIVE_REQUEST),
        ),
        Index(
            "ix_translation_requests_webhook",
            "webhook_next_attempt",
            postgresql_where=text(PENDING_WEBHOOK),
            sqlite_where=text(PENDING_WEBHOOK),
        ),
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_id: Mapped[str | None] = mapped_column(ForeignKey("api_tokens.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    series_id: Mapped[str | None] = mapped_column(ForeignKey("series.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    # queued (waits for the volume to be free) | running | imported | completed | completed_with_residuals
    # | failed | cancelled
    status: Mapped[str] = mapped_column(String(30), default="queued")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    chapter_ids: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)
    finished_at: Mapped[float | None] = mapped_column(Float)
    # Completion report and stored result (app.engines.delivery), written when the request ends.
    report: Mapped[dict | None] = mapped_column(JSON)
    artifact: Mapped[dict | None] = mapped_column(JSON)
    # Webhook: "" (none) | pending | delivered | failed; sent by the worker, never by the API.
    callback_url: Mapped[str | None] = mapped_column(String(2000))
    webhook_state: Mapped[str] = mapped_column(String(20), default="", server_default="")
    webhook_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    webhook_next_attempt: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    webhook_error: Mapped[str] = mapped_column(Text, default="", server_default="")


class ImportSession(Identified, Base):
    """Files uploaded for inspection; nothing becomes a project before the commit."""

    __tablename__ = "import_sessions"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(10))
    # [{"index", "name", "size", "sha256", "path", "inspection"}]; files live under DATA_DIR/staging.
    files: Mapped[list] = mapped_column(JSON, default=list)
    # The commit's answer, returned again when the same commit is repeated.
    result: Mapped[dict | None] = mapped_column(JSON)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


PENDING_EVENT = "state = 'pending'"


class WebhookEvent(Identified, Base):
    """A progress webhook of a request (`chapters.translated`): one row per batch, sent by the worker with
    the signing, allow-list and retries of the request's final webhook."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence", name="uq_webhook_event_sequence"),
        Index(
            "ix_webhook_events_due",
            "next_attempt",
            postgresql_where=text(PENDING_EVENT),
            sqlite_where=text(PENDING_EVENT),
        ),
    )
    request_id: Mapped[str] = mapped_column(
        ForeignKey("translation_requests.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(50))
    # 1, 2, 3… per request, in the order the batches were found.
    sequence: Mapped[int] = mapped_column(Integer)
    chapter_ids: Mapped[list] = mapped_column(JSON, default=list)
    # pending | delivered | failed
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    delivered_at: Mapped[float | None] = mapped_column(Float)
