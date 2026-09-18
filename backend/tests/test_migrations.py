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
    # Through the constraint migration that used to be PostgreSQL-only, and back.
    alembic(database, "downgrade", "b752316870c4")
    alembic(database, "upgrade", "head")
    assert "No new upgrade operations detected" in alembic(database, "check")


def test_existing_navigation_chapters_are_recognised_by_name(tmp_path):
    import sqlite3

    database = tmp_path / "chapters.db"
    alembic(database, "upgrade", "a7c2e9d41f05")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, admin, created_at) VALUES ('u', 'u', 'x', 0, 0)"
        )
        connection.execute(
            "INSERT INTO projects (id, owner_id, title, author, source_language, target_language, quality, "
            "context_backend, status, original_hash, original_path, book_info, config, instructions, bible, "
            "bible_validated, memory_revision, updated_at, created_at, series_name) VALUES ('p', 'u', 't', '', "
            "'en', 'fr', 'normal', 'internal', 'pending', 'h', '/x', '{}', '{}', '', '{}', 0, 0, 0, 0, '')"
        )
        for number, resource in enumerate(
            ("OEBPS/nav.xhtml", "OEBPS/toc.ncx", "OEBPS/c1.xhtml", "OEBPS/navy.xhtml")
        ):
            connection.execute(
                "INSERT INTO chapters (id, project_id, position, title, resource, summary, instructions, analyzed, "
                f"created_at) VALUES ('c{number}', 'p', {number}, 't', '{resource}', '{{}}', '', 0, 0)"
            )
    alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        kinds = dict(connection.execute("SELECT resource, kind FROM chapters"))
    assert kinds == {
        "OEBPS/nav.xhtml": "navigation",
        "OEBPS/toc.ncx": "navigation",
        "OEBPS/c1.xhtml": "narrative",
        "OEBPS/navy.xhtml": "narrative",
    }


def test_existing_passages_join_the_translation_memory(tmp_path):
    import json
    import sqlite3

    from app.engines.translation.memory import memory_key

    database = tmp_path / "memory.db"
    alembic(database, "upgrade", "059d89ae2e77")
    units = [{"id": "u", "text": "Chapter One ⟦x0⟧"}]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (id, username, password_hash, admin, created_at) VALUES ('u', 'u', 'x', 0, 0)"
        )
        connection.execute(
            "INSERT INTO projects (id, owner_id, title, author, source_language, target_language, quality, "
            "context_backend, status, original_hash, original_path, book_info, config, instructions, bible, "
            "bible_validated, memory_revision, updated_at, created_at, series_name) VALUES ('p', 'u', 't', '', "
            "'en', 'fr', 'normal', 'internal', 'pending', 'h', '/x', '{}', '{}', '', '{}', 0, 0, 0, 0, '')"
        )
        connection.execute(
            "INSERT INTO chapters (id, project_id, position, title, resource, summary, instructions, analyzed, "
            "created_at) VALUES ('c', 'p', 0, 't', 'c.xhtml', '{}', '', 0, 0)"
        )
        connection.execute(
            "INSERT INTO segments (id, project_id, chapter_id, position, section, source, units, translation, "
            "translated_units, status, stage, human, retained_source, validated, revision, instructions, "
            "uncertainties, critique, narrative, error, created_at) VALUES ('s', 'p', 'c', 0, '', 'x', ?, '', "
            "'[]', 'pending', 'pending', 0, 0, 0, 0, '', '[]', '[]', '{}', '', 0)",
            (json.dumps(units),),
        )
    alembic(database, "upgrade", "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT source_key FROM segments").fetchone()[0] == memory_key(units)
