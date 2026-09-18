import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def alembic(database: Path, *arguments: str) -> str:
    env = dict(
        os.environ,
        DATABASE_URL=f"sqlite:///{database}",
        DATA_DIR=str(database.parent),
        SECRET_KEY="test-only-secret-key-with-more-than-32-characters",
        BOOTSTRAP_PASSWORD="test-password-123456789",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *arguments], cwd=BACKEND, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_migrations_run_both_ways_on_sqlite_the_default_database(tmp_path):
    database = tmp_path / "fresh.db"
    alembic(database, "upgrade", "head")
    # Every migration must be reversible, including the constraint one that used to be PostgreSQL-only.
    alembic(database, "downgrade", "base")
    alembic(database, "upgrade", "head")
    assert "No new upgrade operations detected" in alembic(database, "check")
