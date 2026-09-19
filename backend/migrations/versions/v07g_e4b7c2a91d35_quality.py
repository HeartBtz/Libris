"""Quality score of each passage (0–100) with the signals it was computed from.

Rows are written by the application whenever a passage or what is known about it changes; passages
translated before this version are scored the first time their book's quality is read.
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b7c2a91d35"
down_revision = "9d3e5b1f7a24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "passage_quality",
        sa.Column(
            "segment_id", sa.String(36), sa.ForeignKey("segments.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "chapter_id", sa.String(36), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(10), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.Float(), nullable=False),
    )
    op.create_index("ix_passage_quality_project_score", "passage_quality", ["project_id", "score"])
    op.create_index("ix_passage_quality_chapter_id", "passage_quality", ["chapter_id"])


def downgrade():
    op.drop_index("ix_passage_quality_chapter_id", table_name="passage_quality")
    op.drop_index("ix_passage_quality_project_score", table_name="passage_quality")
    op.drop_table("passage_quality")
