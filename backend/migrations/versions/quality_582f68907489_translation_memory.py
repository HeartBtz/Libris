"""Translation memory: an index of normalized passage sources (the switch lives in projects.config)."""

import hashlib
import json
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "582f68907489"
down_revision = "059d89ae2e77"
branch_labels = None
depends_on = None

BATCH = 500


def memory_key(units: list[dict]) -> str:
    # Same normalization as app.engines.translation.memory.memory_key, frozen here with the migration.
    normalized = [" ".join(unicodedata.normalize("NFKC", unit["text"]).split()) for unit in units]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()


def upgrade():
    op.add_column("segments", sa.Column("source_key", sa.String(64), nullable=True))
    op.create_index("ix_segments_source_key", "segments", ["source_key"])
    # Passages translated before this version are part of the memory from the start.
    segments = sa.table(
        "segments", sa.column("id", sa.String), sa.column("units", sa.JSON), sa.column("source_key")
    )
    connection = op.get_bind()
    last = ""
    while True:
        rows = connection.execute(
            sa.select(segments.c.id, segments.c.units)
            .where(segments.c.id > last)
            .order_by(segments.c.id)
            .limit(BATCH)
        ).all()
        if not rows:
            break
        for row in rows:
            units = row.units if isinstance(row.units, list) else json.loads(row.units or "[]")
            connection.execute(
                segments.update().where(segments.c.id == row.id).values(source_key=memory_key(units))
            )
        last = rows[-1].id


def downgrade():
    op.drop_index("ix_segments_source_key", table_name="segments")
    with op.batch_alter_table("segments") as batch:
        batch.drop_column("source_key")
