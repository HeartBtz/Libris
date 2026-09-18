import io
import zipfile

import httpx
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.projects import import_book
from app.config import settings
from app.db import SessionLocal
from app.engines.translation import final_review
from app.main import app
from app.models import Membership, Project, Provider, User
from app.security import decrypt, encrypt, password_hash

PASSWORD = "test-password-123456789"


def login(client, username="tester"):
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200


def add_user(username: str, admin: bool = False) -> str:
    with SessionLocal() as db:
        user = User(username=username, password_hash=password_hash(PASSWORD), admin=admin)
        db.add(user)
        db.commit()
        return user.id


def test_the_api_map_is_for_signed_in_users_and_can_be_disabled(seeded, monkeypatch):
    with TestClient(app) as client:
        assert client.get("/openapi.json").status_code == 401
        login(client)
        schema = client.get("/openapi.json")
        assert schema.status_code == 200 and "/api/projects" in schema.json()["paths"]
        monkeypatch.setattr(settings(), "openapi_enabled", False)
        assert client.get("/openapi.json").status_code == 404


def test_only_administrators_see_provider_addresses(seeded):
    add_user("reader")
    with TestClient(app) as client:
        login(client, "reader")
        [provider] = client.get("/api/providers").json()
        assert provider == {
            "id": seeded[2], "created_at": provider["created_at"], "kind": "openai", "name": "Mock",
            "model": "test-model",
        }  # fmt: skip
        login(client)
        assert client.get("/api/providers").json()[0]["base_url"] == "https://llm.test/v1"


def test_moving_a_provider_with_a_stored_key_requires_the_key_again(seeded):
    provider_id = seeded[2]
    with SessionLocal() as db:
        db.get(Provider, provider_id).encrypted_key = encrypt("sk-stored")
        db.commit()
    body = {"name": "Mock", "base_url": "https://llm.test/v1", "model": "test-model", "context_window": 64000}
    with TestClient(app) as client:
        login(client)
        assert client.put(f"/api/providers/{provider_id}", json=dict(body, name="Renamed")).status_code == 200
        moved = client.put(f"/api/providers/{provider_id}", json=dict(body, base_url="https://evil.test/v1"))
        assert moved.status_code == 409 and "Ressaisissez la clé API" in moved.json()["detail"]
        retyped = client.put(f"/api/providers/{provider_id}", json=dict(body, kind="anthropic"))
        assert retyped.status_code == 409
        with SessionLocal() as db:
            assert db.get(Provider, provider_id).base_url == "https://llm.test/v1"
        again = client.put(
            f"/api/providers/{provider_id}", json=dict(body, base_url="https://new.test/v1", api_key="sk-new")
        )
        assert again.status_code == 200
    with SessionLocal() as db:
        assert decrypt(db.get(Provider, provider_id).encrypted_key) == "sk-new"


@respx.mock
async def test_web_search_ignores_proxy_settings_and_bounds_answers(monkeypatch):
    monkeypatch.setattr(settings(), "searxng_url", "https://search.test")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    clients = []
    real = httpx.AsyncClient

    def recording(*arguments, **options):
        clients.append(options)
        return real(*arguments, **options)

    monkeypatch.setattr(final_review.httpx, "AsyncClient", recording)
    result = {"url": "https://example.test/a", "title": "A", "content": "B"}
    respx.get("https://search.test/search", params={"q": "small"}).respond(200, json={"results": [result]})
    padding = "x" * (final_review.SEARCH_RESPONSE_LIMIT + 10)
    respx.get("https://search.test/search", params={"q": "huge"}).respond(
        200, json={"results": [result], "padding": padding}
    )
    small = await final_review.web_evidence(["small"])
    huge = await final_review.web_evidence(["huge"])
    assert all(options.get("trust_env") is False for options in clients)
    assert small["sources"] and not small["unavailable"]
    assert huge["sources"] == [] and huge["unavailable"]


def own_book(book: bytes, owner_id: str, series: str, number: int) -> str:
    data = io.BytesIO(book)
    with zipfile.ZipFile(data, "a") as archive:
        archive.comment = f"{series} {number}".encode()
    with SessionLocal() as db:
        project = import_book(db, owner_id, data.getvalue())
        project.series_name, project.volume_number = series, number
        db.commit()
        return project.id


def test_an_editor_cannot_join_a_series_holding_books_they_cannot_read(seeded, book_bytes):
    pid, owner_id, _ = seeded
    editor_id = add_user("editor")
    private = own_book(book_bytes, owner_id, "Private Saga", 1)
    shared = own_book(book_bytes, owner_id, "Shared Saga", 1)
    with SessionLocal() as db:
        db.add(Membership(project_id=pid, user_id=editor_id, role="editor"))
        db.add(Membership(project_id=shared, user_id=editor_id, role="reader"))
        db.commit()
        config = {
            key: getattr(db.get(Project, pid), key)
            for key in ("title", "author", "source_language", "target_language", "quality", "context_backend")
        }
    with TestClient(app) as client:
        login(client, "editor")
        for name in ("Private Saga", "  private   saga "):
            refused = client.put(f"/api/projects/{pid}", json=dict(config, series_name=name, volume_number=2))
            assert refused.status_code == 403, name
        batch = client.put(
            "/api/projects/batch/series", json={"project_ids": [pid], "series_name": "Private Saga"}
        )
        assert batch.status_code == 403
        joined = client.put(f"/api/projects/{pid}", json=dict(config, series_name="Shared Saga", volume_number=2))
        assert joined.status_code == 200
        login(client)
        assert client.put(
            f"/api/projects/{pid}", json=dict(config, series_name="Private Saga", volume_number=2)
        ).status_code == 200
    with SessionLocal() as db:
        assert db.get(Project, pid).series_name == "Private Saga"
        assert db.scalar(select(Project.series_name).where(Project.id == private)) == "Private Saga"
