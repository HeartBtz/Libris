"""Removals of OpenViking documents left behind by deleted volumes and series (`openviking_cleanups`).

The table starts empty: nothing is removed from OpenViking until an administrator switches the cleanup
on (`OPENVIKING_CLEANUP_ON_DELETE` or Settings › Memory · OpenViking) or cleans orphans found by a
dry run.
"""

import sqlalchemy as sa
from alembic import op

revision = "a3f6c1d82e57"
down_revision = "9d3e5b1f7a24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "openviking_cleanups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("label", sa.String(length=500), nullable=False),
        sa.Column("requested_by", sa.String(length=36), nullable=False),
        sa.Column("root_uri", sa.Text(), nullable=False),
        sa.Column("uris", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt", sa.Float(), nullable=False),
        sa.Column("lease_until", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_openviking_cleanups_status", "openviking_cleanups", ["status"])


def downgrade():
    op.drop_index("ix_openviking_cleanups_status", table_name="openviking_cleanups")
    op.drop_table("openviking_cleanups")
    op.execute("DELETE FROM app_settings WHERE key = 'openviking_cleanup'")
