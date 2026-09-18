"""Series library: series, source files, series memory, API tokens and automation requests.

Existing books are grouped by owner and normalized series name into `series` rows; books without a
series stay standalone volumes. Every EPUB gets a `source_assets` row pointing at the file it already
has (no file is read, moved or rewritten) and its chapters point at it. Identifiers of projects,
chapters and passages are untouched, so nothing is translated again.
"""

import json
import time
import uuid

import sqlalchemy as sa
from alembic import op

revision = "5e7b0c1d9a42"
down_revision = "582f68907489"
branch_labels = None
depends_on = None

SERIAL = "project_kind = 'serial'"
LIVE_REQUEST = "status IN ('queued','running')"


def uid() -> str:
    return str(uuid.uuid4())


def normalized(name: str) -> str:
    # Same folding as app.engines.ingestion.naming.normalize_series, frozen with the migration.
    return " ".join((name or "").split()).casefold()


def created():
    return sa.Column("created_at", sa.Float(), nullable=False)


def upgrade():
    op.create_table(
        "series",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("normalized_name", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="books"),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("source_language", sa.String(80), nullable=True),
        sa.Column("target_language", sa.String(80), nullable=True),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("providers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quality", sa.String(30), nullable=True),
        sa.Column("context_backend", sa.String(20), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
        sa.Column("bible", sa.JSON(), nullable=False),
        sa.Column("bible_validated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.Float(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        created(),
        sa.UniqueConstraint("owner_id", "normalized_name", name="uq_series_owner_name"),
    )
    op.create_index("ix_series_owner_id", "series", ["owner_id"])

    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("series_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_format", sa.String(10), nullable=False, server_default="epub"))
        batch.add_column(sa.Column("project_kind", sa.String(10), nullable=False, server_default="volume"))
        batch.add_column(sa.Column("external_id", sa.String(200), nullable=True))
        batch.add_column(sa.Column("import_meta", sa.JSON(), nullable=False, server_default="{}"))
        batch.create_foreign_key("fk_projects_series_id_series", "series", ["series_id"], ["id"])
        batch.create_unique_constraint("uq_projects_series_external", ["series_id", "external_id"])
    op.create_index("ix_projects_series_id", "projects", ["series_id"])
    op.create_index(
        "uq_projects_series_serial",
        "projects",
        ["series_id"],
        unique=True,
        postgresql_where=sa.text(SERIAL),
        sqlite_where=sa.text(SERIAL),
    )

    op.create_table(
        "source_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        created(),
    )
    op.create_index("ix_source_assets_project_id", "source_assets", ["project_id"])
    op.create_index("ix_source_assets_sha256", "source_assets", ["sha256"])

    with op.batch_alter_table("chapters") as batch:
        batch.add_column(sa.Column("source_asset_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("external_id", sa.String(200), nullable=True))
        batch.add_column(sa.Column("chapter_number", sa.Float(), nullable=True))
        batch.add_column(sa.Column("source_checksum", sa.String(64), nullable=True))
        batch.add_column(sa.Column("import_meta", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("context_stale", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_foreign_key(
            "fk_chapters_source_asset_id_source_assets",
            "source_assets",
            ["source_asset_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint("uq_chapters_project_external", ["project_id", "external_id"])
    op.create_index("ix_chapters_source_asset_id", "chapters", ["source_asset_id"])

    with op.batch_alter_table("glossary") as batch:
        batch.add_column(sa.Column("series_override", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("memory_outbox") as batch:
        batch.add_column(sa.Column("uri", sa.Text(), nullable=True))

    op.create_table(
        "series_entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("series_id", sa.String(36), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "first_project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_volume_number", sa.Integer(), nullable=True),
        sa.Column("first_position", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column(
            "merged_into_id",
            sa.String(36),
            sa.ForeignKey("series_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("updated_at", sa.Float(), nullable=False),
        created(),
        sa.UniqueConstraint("series_id", "category", "name", name="uq_series_entity_name"),
    )
    op.create_index("ix_series_entities_series_id", "series_entities", ["series_id"])
    op.create_table(
        "series_entity_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "series_entity_id",
            sa.String(36),
            sa.ForeignKey("series_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("human", sa.Boolean(), nullable=False, server_default=sa.false()),
        created(),
        sa.UniqueConstraint("entity_id", "series_entity_id", name="uq_series_entity_link"),
    )
    for column in ("series_entity_id", "entity_id", "project_id"):
        op.create_index(f"ix_series_entity_links_{column}", "series_entity_links", [column])
    op.create_table(
        "series_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("series_id", sa.String(36), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "source_id", sa.String(36), sa.ForeignKey("series_entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "target_id", sa.String(36), sa.ForeignKey("series_entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column(
            "first_project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_volume_number", sa.Integer(), nullable=True),
        sa.Column("first_position", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("validated", sa.Boolean(), nullable=False, server_default=sa.false()),
        created(),
        sa.UniqueConstraint("series_id", "source_id", "target_id", "relation_type", name="uq_series_relation"),
    )
    for column in ("series_id", "source_id", "target_id"):
        op.create_index(f"ix_series_relations_{column}", "series_relations", [column])
    op.create_table(
        "series_glossary",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("series_id", sa.String(36), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(300), nullable=False),
        sa.Column("translation", sa.String(300), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False, server_default="volume"),
        sa.Column(
            "first_project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("first_volume_number", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        created(),
        sa.UniqueConstraint("series_id", "source", name="uq_series_term_source"),
    )
    op.create_index("ix_series_glossary_series_id", "series_glossary", ["series_id"])
    op.create_table(
        "audit_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("series_id", sa.String(36), sa.ForeignKey("series.id", ondelete="CASCADE"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        created(),
    )
    for column in ("owner_id", "series_id", "project_id"):
        op.create_index(f"ix_audit_entries_{column}", "audit_entries", [column])
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.Float(), nullable=True),
        sa.Column("last_used_at", sa.Float(), nullable=True),
        created(),
    )
    op.create_index("ix_api_tokens_owner_id", "api_tokens", ["owner_id"])
    op.create_table(
        "translation_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_id", sa.String(36), sa.ForeignKey("api_tokens.id", ondelete="SET NULL"), nullable=True),
        sa.Column("external_id", sa.String(200), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("series_id", sa.String(36), sa.ForeignKey("series.id", ondelete="SET NULL"), nullable=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("chapter_ids", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        created(),
        sa.UniqueConstraint("owner_id", "external_id", name="uq_translation_request_external"),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_translation_request_idempotency"),
    )
    for column in ("owner_id", "series_id", "project_id"):
        op.create_index(f"ix_translation_requests_{column}", "translation_requests", [column])
    op.create_index(
        "ix_translation_requests_live",
        "translation_requests",
        ["status"],
        postgresql_where=sa.text(LIVE_REQUEST),
        sqlite_where=sa.text(LIVE_REQUEST),
    )
    op.create_table(
        "import_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.Float(), nullable=False),
        created(),
    )
    op.create_index("ix_import_sessions_owner_id", "import_sessions", ["owner_id"])
    op.create_index("ix_import_sessions_expires_at", "import_sessions", ["expires_at"])
    migrate_existing_books()


def migrate_existing_books():
    connection = op.get_bind()
    projects = sa.table(
        "projects",
        sa.column("id", sa.String),
        sa.column("owner_id", sa.String),
        sa.column("title", sa.String),
        sa.column("author", sa.String),
        sa.column("series_name", sa.String),
        sa.column("series_id", sa.String),
        sa.column("source_language", sa.String),
        sa.column("target_language", sa.String),
        sa.column("original_hash", sa.String),
        sa.column("original_path", sa.Text),
        sa.column("book_info", sa.JSON),
        sa.column("import_meta", sa.JSON),
        sa.column("created_at", sa.Float),
    )
    series = sa.table(
        "series",
        sa.column("id", sa.String),
        sa.column("owner_id", sa.String),
        sa.column("name", sa.String),
        sa.column("normalized_name", sa.String),
        sa.column("kind", sa.String),
        sa.column("authors", sa.JSON),
        sa.column("source_language", sa.String),
        sa.column("target_language", sa.String),
        sa.column("instructions", sa.Text),
        sa.column("bible", sa.JSON),
        sa.column("updated_at", sa.Float),
        sa.column("created_at", sa.Float),
    )
    assets = sa.table(
        "source_assets",
        sa.column("id", sa.String),
        sa.column("project_id", sa.String),
        sa.column("format", sa.String),
        sa.column("original_name", sa.String),
        sa.column("media_type", sa.String),
        sa.column("storage_path", sa.Text),
        sa.column("size", sa.BigInteger),
        sa.column("sha256", sa.String),
        sa.column("meta", sa.JSON),
        sa.column("created_at", sa.Float),
    )
    chapters = sa.table("chapters", sa.column("project_id", sa.String), sa.column("source_asset_id", sa.String))
    rows = connection.execute(
        sa.select(
            projects.c.id,
            projects.c.owner_id,
            projects.c.title,
            projects.c.author,
            projects.c.series_name,
            projects.c.source_language,
            projects.c.target_language,
            projects.c.original_hash,
            projects.c.original_path,
            projects.c.book_info,
            projects.c.created_at,
        ).order_by(projects.c.created_at)
    ).all()
    groups: dict[tuple[str, str], list] = {}
    for row in rows:
        key = normalized(row.series_name)
        if key:
            groups.setdefault((row.owner_id, key), []).append(row)
    for (owner_id, key), books in groups.items():
        series_id = uid()
        languages = {(book.source_language, book.target_language) for book in books}
        source, target = next(iter(languages)) if len(languages) == 1 else (None, None)
        connection.execute(
            series.insert().values(
                id=series_id,
                owner_id=owner_id,
                # The oldest book's spelling names the series.
                name=" ".join(books[0].series_name.split())[:500],
                normalized_name=key[:500],
                kind="books",
                authors=list(dict.fromkeys(book.author for book in books if book.author))[:20],
                source_language=source,
                target_language=target,
                instructions="",
                bible={},
                updated_at=time.time(),
                created_at=min(book.created_at or time.time() for book in books),
            )
        )
        connection.execute(
            projects.update().where(projects.c.id.in_([book.id for book in books])).values(series_id=series_id)
        )
    for row in rows:
        info = row.book_info if isinstance(row.book_info, dict) else json.loads(row.book_info or "{}")
        asset_id = uid()
        connection.execute(
            assets.insert().values(
                id=asset_id,
                project_id=row.id,
                format="epub",
                original_name=f"{(row.title or 'book')[:490]}.epub",
                media_type="application/epub+zip",
                # Books keep their deterministic place; the stored absolute path stays the first guess.
                storage_path=row.original_path or f"books/{row.id}.epub",
                size=int(info.get("size") or 0),
                sha256=row.original_hash or "",
                meta={"migrated_from": "0.5", "fallback_path": f"books/{row.id}.epub"},
                created_at=row.created_at or time.time(),
            )
        )
        connection.execute(chapters.update().where(chapters.c.project_id == row.id).values(source_asset_id=asset_id))
        connection.execute(
            projects.update()
            .where(projects.c.id == row.id)
            .values(import_meta={"version": 1, "adapter": "epub", "migrated_from": "0.5"})
        )


def downgrade():
    # TXT and JSON volumes stay in the tables (their passages and translations are intact), but 0.5
    # cannot export them: it only knows EPUB sources.
    op.drop_index("ix_import_sessions_expires_at", table_name="import_sessions")
    op.drop_index("ix_import_sessions_owner_id", table_name="import_sessions")
    op.drop_table("import_sessions")
    op.drop_index("ix_translation_requests_live", table_name="translation_requests")
    for column in ("owner_id", "series_id", "project_id"):
        op.drop_index(f"ix_translation_requests_{column}", table_name="translation_requests")
    op.drop_table("translation_requests")
    op.drop_index("ix_api_tokens_owner_id", table_name="api_tokens")
    op.drop_table("api_tokens")
    for column in ("owner_id", "series_id", "project_id"):
        op.drop_index(f"ix_audit_entries_{column}", table_name="audit_entries")
    op.drop_table("audit_entries")
    op.drop_index("ix_series_glossary_series_id", table_name="series_glossary")
    op.drop_table("series_glossary")
    for column in ("series_id", "source_id", "target_id"):
        op.drop_index(f"ix_series_relations_{column}", table_name="series_relations")
    op.drop_table("series_relations")
    for column in ("series_entity_id", "entity_id", "project_id"):
        op.drop_index(f"ix_series_entity_links_{column}", table_name="series_entity_links")
    op.drop_table("series_entity_links")
    op.drop_index("ix_series_entities_series_id", table_name="series_entities")
    op.drop_table("series_entities")
    with op.batch_alter_table("memory_outbox") as batch:
        batch.drop_column("uri")
    with op.batch_alter_table("glossary") as batch:
        batch.drop_column("series_override")
    op.drop_index("ix_chapters_source_asset_id", table_name="chapters")
    with op.batch_alter_table("chapters") as batch:
        batch.drop_constraint("uq_chapters_project_external", type_="unique")
        batch.drop_constraint("fk_chapters_source_asset_id_source_assets", type_="foreignkey")
        for column in ("context_stale", "import_meta", "source_checksum", "chapter_number", "external_id"):
            batch.drop_column(column)
        batch.drop_column("source_asset_id")
    op.drop_index("ix_source_assets_sha256", table_name="source_assets")
    op.drop_index("ix_source_assets_project_id", table_name="source_assets")
    op.drop_table("source_assets")
    op.drop_index("uq_projects_series_serial", table_name="projects")
    op.drop_index("ix_projects_series_id", table_name="projects")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("uq_projects_series_external", type_="unique")
        batch.drop_constraint("fk_projects_series_id_series", type_="foreignkey")
        for column in ("import_meta", "external_id", "project_kind", "source_format"):
            batch.drop_column(column)
        batch.drop_column("series_id")
    op.drop_index("ix_series_owner_id", table_name="series")
    op.drop_table("series")
