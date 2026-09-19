"""Delivery of automation requests: completion report, stored result and webhook state.

Existing requests keep their status; their report is computed again on demand and no webhook is sent
for them (no callback address was ever recorded).
"""

import sqlalchemy as sa
from alembic import op

revision = "7c4e2a9d1b63"
down_revision = "7c4e2a9b1f30"
branch_labels = None
depends_on = None

PENDING_WEBHOOK = "webhook_state = 'pending'"


def upgrade():
    with op.batch_alter_table("translation_requests") as batch:
        batch.add_column(sa.Column("finished_at", sa.Float(), nullable=True))
        batch.add_column(sa.Column("report", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("artifact", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("callback_url", sa.String(2000), nullable=True))
        batch.add_column(sa.Column("webhook_state", sa.String(20), nullable=False, server_default=""))
        batch.add_column(sa.Column("webhook_attempts", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("webhook_next_attempt", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("webhook_error", sa.Text(), nullable=False, server_default=""))
    op.create_index(
        "ix_translation_requests_webhook",
        "translation_requests",
        ["webhook_next_attempt"],
        postgresql_where=sa.text(PENDING_WEBHOOK),
        sqlite_where=sa.text(PENDING_WEBHOOK),
    )
    with op.batch_alter_table("api_tokens") as batch:
        batch.add_column(sa.Column("webhook_secret", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("api_tokens") as batch:
        batch.drop_column("webhook_secret")
    op.drop_index("ix_translation_requests_webhook", table_name="translation_requests")
    with op.batch_alter_table("translation_requests") as batch:
        for column in (
            "webhook_error", "webhook_next_attempt", "webhook_attempts", "webhook_state", "callback_url",
            "artifact", "report", "finished_at",
        ):  # fmt: skip
            batch.drop_column(column)
