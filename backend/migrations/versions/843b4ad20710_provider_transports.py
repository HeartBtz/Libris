"""Add provider transports while preserving existing OpenAI-compatible providers."""

from alembic import op
import sqlalchemy as sa

revision = "843b4ad20710"
down_revision = "73842ec66052"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("providers", sa.Column("kind", sa.String(30), nullable=False, server_default="openai"))


def downgrade():
    op.drop_column("providers", "kind")
