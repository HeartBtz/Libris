"""Track explicit source retention without claiming a segment was translated."""

from alembic import op
import sqlalchemy as sa

revision = "b752316870c4"
down_revision = "a64d2ce84277"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "segments", sa.Column("retained_source", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    op.drop_column("segments", "retained_source")
