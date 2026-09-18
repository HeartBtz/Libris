"""Per-passage job state moves from `jobs.checkpoint` to `job_segment_state`; checkpoints keep a cursor.

`jobs.finished_at` records when a job ended, so that the retention can prune the state of old jobs.

Existing checkpoints are converted in place, so a paused job resumes without retranslating anything and
the review history of finished jobs stays visible. The downgrade rebuilds the former lists.
"""

import sqlalchemy as sa
from alembic import op

revision = "b856c2e068f8"
down_revision = "a7c2e9d41f05"
branch_labels = None
depends_on = None

LISTS = {
    "finished_ids": "finished",
    "started_ids": "started",
    "final_review_targets": "review_target",
    "automatic_recovery_targets": "recovery_target",
}
BATCHES = {"consistency_batches": "consistency", "analysis_batches": "bible"}
LEGACY = (*LISTS, *BATCHES, "final_review_done", "final_review_outcomes", "repair", "repair_progress")

jobs = sa.table("jobs", sa.column("id", sa.String), sa.column("checkpoint", sa.JSON))
segments = sa.table("segments", sa.column("id", sa.String), sa.column("position", sa.Integer))
state = sa.table(
    "job_segment_state",
    sa.column("job_id", sa.String),
    sa.column("step", sa.String),
    sa.column("segment_id", sa.String),
    sa.column("key", sa.String),
    sa.column("outcome", sa.String),
    sa.column("data", sa.JSON),
)


def split(job_id: str, checkpoint: dict) -> tuple[dict, list[dict]]:
    rows: dict[tuple, dict] = {}

    def add(step, segment_id="", key="", outcome="", data=None):
        rows[(step, segment_id, key)] = {
            "job_id": job_id,
            "step": step,
            "segment_id": segment_id,
            "key": key,
            "outcome": outcome,
            "data": data or {},
        }

    for name, step in LISTS.items():
        for segment_id in checkpoint.get(name) or []:
            add(step, segment_id)
    outcomes = checkpoint.get("final_review_outcomes") or {}
    for segment_id in [*(checkpoint.get("final_review_done") or []), *outcomes]:
        data = dict(outcomes.get(segment_id) or {})
        add("reviewed", segment_id, outcome=str(data.pop("outcome", ""))[:30], data=data)
    for name, parts in (checkpoint.get("repair") or {}).items():
        segment_id, _, rest = name.partition(":")
        for start, data in parts.items():
            add("repair", segment_id, f"{rest}:{start}", data=data)
    for name, step in BATCHES.items():
        for key in checkpoint.get(name) or []:
            add(step, key=key)
    compact = {key: value for key, value in checkpoint.items() if key not in LEGACY}
    if "final_review_targets" in checkpoint:
        compact["review_targets"] = len(checkpoint["final_review_targets"] or [])
    return compact, list(rows.values())


def upgrade():
    op.create_table(
        "job_segment_state",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step", sa.String(30), nullable=False),
        sa.Column("segment_id", sa.String(36), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "step", "segment_id", "key"),
    )
    # Jobs finished before this migration count from their creation for the retention of their state.
    op.add_column("jobs", sa.Column("finished_at", sa.Float(), nullable=True))
    connection = op.get_bind()
    for job_id in connection.execute(sa.select(jobs.c.id)).scalars().all():
        # One at a time: a production checkpoint reached 443 KB.
        checkpoint = connection.execute(sa.select(jobs.c.checkpoint).where(jobs.c.id == job_id)).scalar()
        if not isinstance(checkpoint, dict) or not any(key in checkpoint for key in LEGACY):
            continue
        compact, rows = split(job_id, checkpoint)
        for start in range(0, len(rows), 500):
            connection.execute(state.insert(), rows[start : start + 500])
        connection.execute(jobs.update().where(jobs.c.id == job_id).values(checkpoint=compact))


def downgrade():
    connection = op.get_bind()
    rebuilt: dict[str, dict] = {}
    rows = connection.execute(
        sa.select(state.c.job_id, state.c.step, state.c.segment_id, state.c.key, state.c.outcome, state.c.data)
        .select_from(state.outerjoin(segments, segments.c.id == state.c.segment_id))
        .order_by(state.c.job_id, segments.c.position)
    )
    names = {step: name for name, step in {**LISTS, **BATCHES}.items()}
    for job_id, step, segment_id, key, outcome, data in rows:
        legacy = rebuilt.setdefault(job_id, {})
        if step in {"consistency", "bible"}:
            legacy.setdefault(names[step], []).append(key)
        elif step in names:
            legacy.setdefault(names[step], []).append(segment_id)
        elif step == "reviewed":
            legacy.setdefault("final_review_done", []).append(segment_id)
            if outcome:
                legacy.setdefault("final_review_outcomes", {})[segment_id] = {**(data or {}), "outcome": outcome}
        elif step == "repair":
            revision, operation, start = key.split(":")
            name = f"{segment_id}:{revision}:{operation}"
            legacy.setdefault("repair", {}).setdefault(name, {})[start] = data
    for job_id, checkpoint in connection.execute(sa.select(jobs.c.id, jobs.c.checkpoint)).all():
        checkpoint = dict(checkpoint or {})
        if job_id not in rebuilt and "review_targets" not in checkpoint:
            continue
        if "review_targets" in checkpoint:
            checkpoint.pop("review_targets")
            checkpoint.setdefault("final_review_targets", [])
        checkpoint.update(rebuilt.get(job_id, {}))
        connection.execute(jobs.update().where(jobs.c.id == job_id).values(checkpoint=checkpoint))
    op.drop_table("job_segment_state")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("finished_at")
