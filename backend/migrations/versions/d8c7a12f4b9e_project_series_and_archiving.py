"""Add project series metadata and reversible archiving."""

import sqlalchemy as sa
from alembic import op

revision = "d8c7a12f4b9e"
down_revision = "c93b8c242df1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects",
        sa.Column("series_name", sa.String(length=500), nullable=False, server_default=""),
    )
    op.add_column("projects", sa.Column("volume_number", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("archived_at", sa.Float(), nullable=True))
    op.create_index("ix_projects_series_name", "projects", ["series_name"])
    op.create_index("ix_projects_archived_at", "projects", ["archived_at"])


def downgrade():
    op.drop_index("ix_projects_archived_at", table_name="projects")
    op.drop_index("ix_projects_series_name", table_name="projects")
    op.drop_column("projects", "archived_at")
    op.drop_column("projects", "volume_number")
    op.drop_column("projects", "series_name")
