"""Administrators edit prompts as versions and can restore an earlier one or the built-in prompt."""

from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Prompt, User
from app.providers.llm import load_prompt
from app.security import password_hash

PASSWORD = "test-password-123456789"
FIRST = "First custom polishing prompt for {target_language}."
SECOND = "Second custom polishing prompt for {target_language}."


def signed_in(client: TestClient, username: str = "tester") -> TestClient:
    assert (
        client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
    )
    return client


def current(client: TestClient, name: str = "polishing") -> dict:
    return next(p for p in client.get("/api/prompts").json() if p["name"] == name)


def test_prompt_history_is_for_administrators_only(seeded):
    with SessionLocal() as db:
        db.add(User(username="reader", password_hash=password_hash(PASSWORD), admin=False))
        db.commit()
    with TestClient(app) as client:
        assert client.get("/api/prompts/polishing/versions").status_code == 401
        signed_in(client, "reader")
        assert client.get("/api/prompts/polishing/versions").status_code == 403
        assert client.post("/api/prompts/polishing/restore", json={"version": 0}).status_code == 403


def test_restoring_a_version_and_the_built_in_prompt_keeps_the_history(seeded):
    builtin = (settings().prompt_dir / "polishing.txt").read_text()
    with TestClient(app) as client:
        signed_in(client)
        start = current(client)
        assert start["version"] == 0 and start["builtin"] is True and start["content"] == builtin
        assert start["updated_at"] is None
        assert client.put("/api/prompts/polishing", json={"content": FIRST}).status_code == 200
        assert client.put("/api/prompts/polishing", json={"content": SECOND}).status_code == 200

        versions = client.get("/api/prompts/polishing/versions").json()
        assert [(v["version"], v["current"], v["builtin"]) for v in versions] == [
            (2, True, False),
            (1, False, False),
            (0, False, True),
        ]
        assert versions[1]["content"] == FIRST and versions[1]["created_at"] > 0
        assert versions[2]["content"] == builtin and versions[2]["created_at"] is None

        restored = client.post("/api/prompts/polishing/restore", json={"version": 1}).json()
        assert restored["version"] == 3 and restored["content"] == FIRST and restored["builtin"] is False
        system, version = load_prompt("polishing", "en", "fr")
        assert system.startswith("First custom polishing prompt for French (fr).")
        assert version.startswith("db-v3+")

        original = client.post("/api/prompts/polishing/restore", json={"version": 0}).json()
        assert original["version"] == 4 and original["builtin"] is True and original["content"] == builtin
        assert current(client)["builtin"] is True
        system, version = load_prompt("polishing", "en", "fr")
        assert version.startswith("file-v")
        assert "custom polishing" not in system

        # Restoring what is already in force adds no version.
        again = client.post("/api/prompts/polishing/restore", json={"version": 0}).json()
        assert again["version"] == 4
        versions = client.get("/api/prompts/polishing/versions").json()
        assert [v["version"] for v in versions] == [4, 3, 2, 1, 0]
        assert versions[0]["builtin"] is True and versions[0]["current"] is True
        with SessionLocal() as db:
            assert db.query(Prompt).filter(Prompt.name == "polishing").count() == 4

        # A new edit after the restore continues the numbering.
        edited = client.put("/api/prompts/polishing", json={"content": SECOND}).json()
        assert edited["version"] == 5


def test_restoring_an_unknown_prompt_or_version_is_refused(seeded):
    with TestClient(app) as client:
        signed_in(client)
        assert client.get("/api/prompts/missing/versions").status_code == 404
        assert client.post("/api/prompts/missing/restore", json={"version": 0}).status_code == 404
        response = client.post("/api/prompts/polishing/restore", json={"version": 7})
        assert response.status_code == 404
        assert client.post("/api/prompts/polishing/restore", json={"version": -1}).status_code == 422
