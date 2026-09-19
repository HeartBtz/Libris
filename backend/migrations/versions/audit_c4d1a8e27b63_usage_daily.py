"""Daily usage aggregates (`usage_daily`) and the creation-time index of model requests.

The table starts empty: the worker's first retention pass rolls the existing requests up, one day per
transaction (`python -m app.maintenance.usage` does it on demand). Until then the statistics read the
requests themselves, as before.
"""

import sqlalchemy as sa
from alembic import op

revision = "c4d1a8e27b63"
down_revision = "5e7b0c1d9a42"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("cached", sa.Boolean(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "day",
            "project_id",
            "provider_id",
            "operation",
            "model",
            "status",
            "cached",
            name="uq_usage_daily_key",
        ),
    )
    op.create_index("ix_usage_daily_day", "usage_daily", ["day"])
    op.create_index("ix_usage_daily_project_id", "usage_daily", ["project_id"])
    op.create_index("ix_llm_requests_created_at", "llm_requests", ["created_at"])


def downgrade():
    op.drop_index("ix_llm_requests_created_at", table_name="llm_requests")
    op.drop_index("ix_usage_daily_project_id", table_name="usage_daily")
    op.drop_index("ix_usage_daily_day", table_name="usage_daily")
    op.drop_table("usage_daily")
    op.execute("DELETE FROM app_settings WHERE key = 'usage_rollup'")
