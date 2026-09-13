"""Persistent dependency waits and request ownership for interruption handling."""

from alembic import op
import sqlalchemy as sa

revision = "953cb481a722"
down_revision = "843b4ad20710"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("next_attempt", sa.Float(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("outage_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("jobs", sa.Column("stop_reason", sa.String(40), nullable=False, server_default=""))
    active = sa.text(
        "status IN ('pending','waiting','paused','blocked','analyzing','translating','reviewing','syncing')"
    )
    op.create_index(
        "uq_live_job_project",
        "jobs",
        ["project_id"],
        unique=True,
        postgresql_where=active,
        sqlite_where=active,
    )
    with op.batch_alter_table("llm_requests") as batch:
        batch.add_column(sa.Column("job_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("execution_owner", sa.String(36), nullable=False, server_default=""))
        batch.create_foreign_key("fk_request_job", "jobs", ["job_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_llm_requests_job_id", ["job_id"])


def downgrade():
    with op.batch_alter_table("llm_requests") as batch:
        batch.drop_index("ix_llm_requests_job_id")
        batch.drop_constraint("fk_request_job", type_="foreignkey")
        batch.drop_column("execution_owner")
        batch.drop_column("job_id")
    op.drop_index("uq_live_job_project", table_name="jobs")
    for column in ("stop_reason", "outage_count", "next_attempt"):
        op.drop_column("jobs", column)
