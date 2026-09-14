"""Add reversible account deactivation without deleting owned projects."""

import sqlalchemy as sa
from alembic import op

revision = "e92fa613bc10"
down_revision = "d8c7a12f4b9e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column("users", "active")
