"""Cost budgets: a spending cap per API token, and the cost kept on each ended automation request."""

import sqlalchemy as sa
from alembic import op

revision = "4b8e2d6f1c93"
down_revision = "9d3e5b1f7a24"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_tokens", sa.Column("budget_amount", sa.Float(), nullable=True))
    op.add_column(
        "api_tokens", sa.Column("budget_period", sa.String(length=10), nullable=False, server_default="month")
    )
    op.add_column("translation_requests", sa.Column("cost", sa.Float(), nullable=True))
    op.create_index("ix_translation_requests_token", "translation_requests", ["token_id"])


def downgrade():
    op.drop_index("ix_translation_requests_token", table_name="translation_requests")
    with op.batch_alter_table("translation_requests") as batch:
        batch.drop_column("cost")
    with op.batch_alter_table("api_tokens") as batch:
        batch.drop_column("budget_period")
        batch.drop_column("budget_amount")
