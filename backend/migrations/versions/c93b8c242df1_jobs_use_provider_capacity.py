"""Assign jobs to providers for provider-scoped concurrency."""

import sqlalchemy as sa
from alembic import op

revision = "c93b8c242df1"
down_revision = "b752316870c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("provider_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_jobs_provider_id",
        "jobs",
        "providers",
        ["provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    bind = op.get_bind()
    override = (
        "NULLIF(jobs.options->>'provider_id', '')"
        if bind.dialect.name == "postgresql"
        else "NULLIF(json_extract(jobs.options, '$.provider_id'), '')"
    )
    op.execute(
        sa.text(
            f"""
            UPDATE jobs
            SET provider_id = COALESCE(
                {override},
                (SELECT projects.provider_id FROM projects WHERE projects.id = jobs.project_id)
            )
            WHERE operation != 'sync_memory'
            """
        )
    )
    op.create_index("ix_jobs_provider_status", "jobs", ["provider_id", "status"])


def downgrade():
    op.drop_index("ix_jobs_provider_status", table_name="jobs")
    op.drop_constraint("fk_jobs_provider_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "provider_id")
