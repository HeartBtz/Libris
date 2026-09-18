from alembic import context

from app import models  # noqa: F401
from app.db import Base, engine

with engine.connect() as connection:
    if connection.dialect.name == "sqlite":
        # Batch migrations rebuild a table (copy, drop, rename): with foreign keys enforced, dropping
        # `projects` would cascade to every chapter and passage. Set before any transaction starts.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
