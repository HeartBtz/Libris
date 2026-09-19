"""Progress webhooks of automation requests: one row per batch of translated chapters.

A request that asks for `chapters.translated` gets one signed POST per batch of chapters whose passages
are all translated, before the final `translation_request.finished`. Existing requests are unchanged.
"""

import sqlalchemy as sa
from alembic import op

revision = "4b8e1c6d2f90"
down_revision = "9d3e5b1f7a24"
branch_labels = None
depends_on = None

PENDING_EVENT = "state = 'pending'"


def upgrade():
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column(
            "request_id",
            sa.String(36),
            sa.ForeignKey("translation_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(50), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("chapter_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt", sa.Float(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("delivered_at", sa.Float(), nullable=True),
        sa.UniqueConstraint("request_id", "sequence", name="uq_webhook_event_sequence"),
    )
    op.create_index("ix_webhook_events_request_id", "webhook_events", ["request_id"])
    op.create_index(
        "ix_webhook_events_due",
        "webhook_events",
        ["next_attempt"],
        postgresql_where=sa.text(PENDING_EVENT),
        sqlite_where=sa.text(PENDING_EVENT),
    )


def downgrade():
    op.drop_index("ix_webhook_events_due", table_name="webhook_events")
    op.drop_index("ix_webhook_events_request_id", table_name="webhook_events")
    op.drop_table("webhook_events")
