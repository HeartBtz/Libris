"""Chapter kind: navigation documents and non-linear pages are no longer counted as the story's chapters."""

import posixpath
import re

import sqlalchemy as sa
from alembic import op

revision = "059d89ae2e77"
down_revision = "a7c2e9d41f05"
branch_labels = None
depends_on = None

NAVIGATION_NAME = re.compile(r"(?:nav|toc)\.x?html?|.*\.ncx", re.I)


def upgrade():
    op.add_column("chapters", sa.Column("kind", sa.String(20), nullable=False, server_default="narrative"))
    # Books imported earlier did not record which document was the table of contents: the usual
    # file names are the only evidence left.
    chapters = sa.table(
        "chapters", sa.column("id", sa.String), sa.column("resource", sa.Text), sa.column("kind")
    )
    connection = op.get_bind()
    navigation = [
        row.id
        for row in connection.execute(sa.select(chapters.c.id, chapters.c.resource))
        if NAVIGATION_NAME.fullmatch(posixpath.basename(row.resource or ""))
    ]
    for start in range(0, len(navigation), 500):
        connection.execute(
            chapters.update()
            .where(chapters.c.id.in_(navigation[start : start + 500]))
            .values(kind="navigation")
        )


def downgrade():
    with op.batch_alter_table("chapters") as batch:
        batch.drop_column("kind")
