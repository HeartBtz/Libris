"""Fair multi-user queue: job priority, requesting token, queue times; per-token queue limits.

Existing jobs become normal priority, queued at their creation time; existing tokens may ask for normal
priority at most and follow their account's quotas.
"""

import sqlalchemy as sa
from alembic import op

revision = "300863c7cbc7"
down_revision = "9d3e5b1f7a24"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("priority", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("token_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("queued_at", sa.Float(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("claimed_at", sa.Float(), nullable=True))
        batch.create_foreign_key("fk_jobs_token_id", "api_tokens", ["token_id"], ["id"], ondelete="SET NULL")
    op.execute(sa.text("UPDATE jobs SET queued_at = created_at"))
    with op.batch_alter_table("api_tokens") as batch:
        batch.add_column(sa.Column("max_priority", sa.String(10), nullable=False, server_default="normal"))
        batch.add_column(sa.Column("max_running", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("max_queued", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("api_tokens") as batch:
        batch.drop_column("max_queued")
        batch.drop_column("max_running")
        batch.drop_column("max_priority")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_token_id", type_="foreignkey")
        batch.drop_column("claimed_at")
        batch.drop_column("queued_at")
        batch.drop_column("token_id")
        batch.drop_column("priority")
