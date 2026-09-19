"""Passages kept in the original are no longer human corrections.

Keeping the source text (`source_retained`) used to mark the passage `human`, which protected it from any
later machine translation and counted it among human corrections in the reports. The flag is cleared on
every passage whose active version is a retained original; `retained_source` keeps them apart.
"""

import sqlalchemy as sa
from alembic import op

revision = "9d3e5b1f7a24"
down_revision = "c4d1a8e27b63"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text("UPDATE segments SET human = :human WHERE retained_source = :retained").bindparams(
            human=False, retained=True
        )
    )


def downgrade():
    op.execute(
        sa.text("UPDATE segments SET human = :human WHERE retained_source = :retained").bindparams(
            human=True, retained=True
        )
    )
