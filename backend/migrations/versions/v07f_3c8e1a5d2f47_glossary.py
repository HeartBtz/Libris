"""Shared glossaries: named terminologies that several series of one owner can follow."""

import sqlalchemy as sa
from alembic import op

revision = "3c8e1a5d2f47"
down_revision = "9d3e5b1f7a24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "shared_glossaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_language", sa.String(80), nullable=True),
        sa.Column("target_language", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("owner_id", "normalized_name", name="uq_shared_glossary_owner_name"),
    )
    op.create_index("ix_shared_glossaries_owner_id", "shared_glossaries", ["owner_id"])
    op.create_table(
        "shared_glossary_terms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "glossary_id",
            sa.String(36),
            sa.ForeignKey("shared_glossaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(300), nullable=False),
        sa.Column("translation", sa.String(300), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.UniqueConstraint("glossary_id", "source", name="uq_shared_term_source"),
    )
    op.create_index("ix_shared_glossary_terms_glossary_id", "shared_glossary_terms", ["glossary_id"])
    op.create_table(
        "series_shared_glossaries",
        sa.Column(
            "series_id", sa.String(36), sa.ForeignKey("series.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "glossary_id",
            sa.String(36),
            sa.ForeignKey("shared_glossaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attached_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_series_shared_glossaries_glossary_id", "series_shared_glossaries", ["glossary_id"])


def downgrade():
    op.drop_index("ix_series_shared_glossaries_glossary_id", table_name="series_shared_glossaries")
    op.drop_table("series_shared_glossaries")
    op.drop_index("ix_shared_glossary_terms_glossary_id", table_name="shared_glossary_terms")
    op.drop_table("shared_glossary_terms")
    op.drop_index("ix_shared_glossaries_owner_id", table_name="shared_glossaries")
    op.drop_table("shared_glossaries")
