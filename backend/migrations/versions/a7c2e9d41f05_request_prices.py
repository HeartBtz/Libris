"""Price in force recorded with each request, so that statistics stop following later price changes."""

import sqlalchemy as sa
from alembic import op

revision = "a7c2e9d41f05"
down_revision = "f3a91c07d2be"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_requests", sa.Column("input_cost", sa.Float(), nullable=True))
    op.add_column("llm_requests", sa.Column("output_cost", sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("llm_requests") as batch:
        batch.drop_column("output_cost")
        batch.drop_column("input_cost")
