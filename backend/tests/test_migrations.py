import json
import os
import sqlite3
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
    # Through the constraint migration that used to be PostgreSQL-only, and back.
    alembic(database, "downgrade", "b752316870c4")
    alembic(database, "upgrade", "head")
    assert "No new upgrade operations detected" in alembic(database, "check")


def test_legacy_checkpoints_become_rows_and_come_back_on_downgrade(tmp_path):
    database = tmp_path / "legacy.db"
    alembic(database, "upgrade", "a7c2e9d41f05")
    legacy = {
        "step": "final_review",
        "current": 2,
        "total": 2,
        "finished_ids": ["s1", "s2"],
        "started_ids": ["s2"],
        "final_review_targets": ["s1", "s2"],
        "final_review_done": ["s1"],
        "final_review_outcomes": {"s1": {"outcome": "resolved", "revised": True}},
        "repair": {"s2:3:translation": {"0": {"units": [{"id": "u", "text": "Bonjour"}]}}},
        "repair_progress": {"segment_id": "s2", "units_done": 4, "units_total": 9},
        "analysis_batches": ["c1:0"],
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO jobs (id, created_at, project_id, operation, status, options, checkpoint, lease_owner,"
            " lease_until, attempts, error, next_attempt, outage_count, stop_reason)"
            " VALUES ('j1', 0, 'p1', 'translate', 'paused', '{}', ?, '', 0, 1, '', 0, 0, '')",
            (json.dumps(legacy),),
        )
    alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        compact = json.loads(connection.execute("SELECT checkpoint FROM jobs").fetchone()[0])
        rows = connection.execute(
            "SELECT step, segment_id, key, outcome, data FROM job_segment_state ORDER BY step, segment_id"
        ).fetchall()
    assert compact == {"step": "final_review", "current": 2, "total": 2, "review_targets": 2}
    assert [(step, sid, key, outcome) for step, sid, key, outcome, _ in rows] == [
        ("bible", "", "c1:0", ""),
        ("finished", "s1", "", ""),
        ("finished", "s2", "", ""),
        ("repair", "s2", "3:translation:0", ""),
        ("review_target", "s1", "", ""),
        ("review_target", "s2", "", ""),
        ("reviewed", "s1", "", "resolved"),
        ("started", "s2", "", ""),
    ]
    assert json.loads(rows[6][4]) == {"revised": True}
    alembic(database, "downgrade", "-1")
    with sqlite3.connect(database) as connection:
        restored = json.loads(connection.execute("SELECT checkpoint FROM jobs").fetchone()[0])
    del legacy["repair_progress"]  # display-only, not worth keeping
    assert restored == legacy
    alembic(database, "upgrade", "head")
    assert "No new upgrade operations detected" in alembic(database, "check")
