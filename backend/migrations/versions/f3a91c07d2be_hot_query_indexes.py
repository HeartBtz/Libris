"""Indexes for the queries that run in a loop: admission, per-passage memories, SSE, outbox, cascades."""

import sqlalchemy as sa
from alembic import op

revision = "f3a91c07d2be"
down_revision = "e92fa613bc10"
branch_labels = None
depends_on = None

RUNNING = sa.text("status = 'running'")
PENDING = sa.text("status <> 'sent'")
PLAIN = (
    ("ix_memories_segment_kind", "memories", ["segment_id", "kind"]),
    ("ix_events_project_id_id", "events", ["project_id", "id"]),
    ("ix_character_relations_source_id", "character_relations", ["source_id"]),
    ("ix_character_relations_target_id", "character_relations", ["target_id"]),
    ("ix_character_relations_segment_id", "character_relations", ["segment_id"]),
    ("ix_entity_merges_target_id", "entity_merges", ["target_id"]),
)


def upgrade():
    op.create_index(
        "ix_llm_requests_running", "llm_requests", ["provider_id"], postgresql_where=RUNNING, sqlite_where=RUNNING
    )
    op.create_index(
        "ix_memory_outbox_pending", "memory_outbox", ["next_attempt"], postgresql_where=PENDING, sqlite_where=PENDING
    )
    for name, table, columns in PLAIN:
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in reversed(PLAIN):
        op.drop_index(name, table_name=table)
    op.drop_index("ix_memory_outbox_pending", table_name="memory_outbox")
    op.drop_index("ix_llm_requests_running", table_name="llm_requests")
