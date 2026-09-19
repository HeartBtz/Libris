"""Autopilot: the log of automatic decisions, and what a job produced.

`autopilot_decisions` records every decision the autopilot takes instead of a person (a proposal
applied or rejected, a passage kept in the original, a fallback provider…) with its reason.
`jobs.result` holds the report of a finished job (`{"autopilot": {...}}`); existing jobs get `{}`.
"""

import sqlalchemy as sa
from alembic import op

revision = "7c4e2a9b1f30"
down_revision = "5e7b0c1d9a42"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("result", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "autopilot_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "segment_id", sa.String(36), sa.ForeignKey("segments.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
    )
    op.create_index(
        "ix_autopilot_decisions_project_created", "autopilot_decisions", ["project_id", "created_at"]
    )
    op.create_index("ix_autopilot_decisions_job_id", "autopilot_decisions", ["job_id"])
    op.create_index("ix_autopilot_decisions_segment_id", "autopilot_decisions", ["segment_id"])


def downgrade():
    op.drop_index("ix_autopilot_decisions_segment_id", table_name="autopilot_decisions")
    op.drop_index("ix_autopilot_decisions_job_id", table_name="autopilot_decisions")
    op.drop_index("ix_autopilot_decisions_project_created", table_name="autopilot_decisions")
    op.drop_table("autopilot_decisions")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("result")
