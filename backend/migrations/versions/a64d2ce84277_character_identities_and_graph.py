"""Canonical identities, merge provenance and narrative relationships."""

from alembic import op
import sqlalchemy as sa

revision = "a64d2ce84277"
down_revision = "953cb481a722"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("entities") as batch:
        batch.add_column(
            sa.Column("identity_validated", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("merged_into_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_entity_canonical", "entities", ["merged_into_id"], ["id"], ondelete="SET NULL"
        )
    op.create_table(
        "entity_merges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "target_id", sa.String(36), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("snapshots", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("human", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_entity_merges_project_id", "entity_merges", ["project_id"])
    op.create_table(
        "character_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "source_id", sa.String(36), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "target_id", sa.String(36), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("segment_id", sa.String(36), sa.ForeignKey("segments.id", ondelete="SET NULL")),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("relation_type", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(30), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_character_relations_project_id", "character_relations", ["project_id"])


def downgrade():
    op.drop_table("character_relations")
    op.drop_table("entity_merges")
    with op.batch_alter_table("entities") as batch:
        batch.drop_constraint("fk_entity_canonical", type_="foreignkey")
        batch.drop_column("merged_into_id")
        batch.drop_column("identity_validated")
