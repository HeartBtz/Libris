"""Assign jobs to providers for provider-scoped concurrency."""

import sqlalchemy as sa
from alembic import op

revision = "c93b8c242df1"
down_revision = "b752316870c4"
branch_labels = None
depends_on = None


def upgrade():
    # Batch mode: SQLite (the default DATABASE_URL) cannot ALTER a table to add a constraint.
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("provider_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_provider_id",
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
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_provider_id", type_="foreignkey")
        batch.drop_column("provider_id")
