import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app, login_attempts
from app.models import User


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    login_attempts.clear()
    yield
    login_attempts.clear()


def login(client, username="tester", password="test-password-123456789"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_password_change_revokes_all_sessions(seeded):
    with TestClient(app) as first, TestClient(app) as second:
        assert login(first).status_code == 200
        assert login(second).status_code == 200
        assert len(first.get("/api/auth/sessions").json()) == 2
        assert first.put("/api/auth/password", json={
            "current_password": "wrong", "new_password": "replacement-password-123",
        }).status_code == 400
        assert first.put("/api/auth/password", json={
            "current_password": "test-password-123456789", "new_password": "replacement-password-123",
        }).status_code == 200
        assert first.get("/api/auth/me").status_code == 401
        assert second.get("/api/auth/me").status_code == 401
        assert login(second).status_code == 401
        assert login(second, password="replacement-password-123").status_code == 200


def test_username_change_preserves_sessions_and_requires_unique_name(seeded):
    with TestClient(app) as client, TestClient(app) as old_login, TestClient(app) as new_login:
        assert login(client).status_code == 200
        response = client.put("/api/auth/username", json={"username": "new-owner"})
        assert response.status_code == 200
        assert response.json()["username"] == "new-owner"
        assert client.get("/api/auth/me").json()["username"] == "new-owner"
        assert login(old_login).status_code == 401
        assert login(new_login, "new-owner").status_code == 200

        duplicate = client.post(
            "/api/users", json={"username": "reader", "password": "reader-password-123"}
        )
        assert duplicate.status_code == 201
        assert client.put("/api/auth/username", json={"username": "reader"}).status_code == 409


def test_account_lifecycle_and_access_control(seeded):
    with TestClient(app) as admin, TestClient(app) as member:
        login(admin)
        response = admin.post("/api/users", json={"username": "reader", "password": "reader-password-123"})
        assert response.status_code == 201
        uid = response.json()["id"]
        assert "password_hash" not in response.json()
        assert admin.post("/api/users", json={"username": "reader", "password": "reader-password-123"}).status_code == 409
        login(member, "reader", "reader-password-123")
        assert member.get("/api/users").status_code == 403
        assert member.put(f"/api/users/{uid}", json={"active": True, "admin": True}).status_code == 403
        assert member.get(f"/api/projects/{seeded[0]}").status_code == 404
        assert admin.put(f"/api/users/{seeded[1]}", json={"active": False, "admin": True}).status_code == 409
        assert admin.put(f"/api/users/{uid}", json={"active": False, "admin": False}).status_code == 200
        assert member.get("/api/auth/me").status_code == 401
        assert login(member, "reader", "reader-password-123").status_code == 401
        assert admin.put(f"/api/users/{uid}", json={"active": True, "admin": False}).status_code == 200
        assert admin.put(f"/api/users/{uid}/password", json={"password": "reset-password-123"}).status_code == 200
        assert login(member, "reader", "reset-password-123").status_code == 200
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.id == uid)).active


def test_logout_only_revokes_current_session(seeded):
    with TestClient(app) as first, TestClient(app) as second:
        login(first)
        login(second)
        assert first.post("/api/auth/logout").status_code == 200
        assert second.get("/api/auth/me").status_code == 200
        current = second.get("/api/auth/sessions").json()[0]
        assert current["current"]
        second.delete(f"/api/auth/sessions/{current['id']}")
        assert second.get("/api/auth/me").status_code == 401


def test_password_change_rejects_cross_site(seeded):
    with TestClient(app) as client:
        login(client)
        assert client.put("/api/auth/password", headers={"Origin": "https://attacker.test"}, json={
            "current_password": "test-password-123456789", "new_password": "replacement-password-123",
        }).status_code == 403


def test_https_session_cookie_is_secure_and_reissued(seeded, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings(), "cookie_secure", True)
    with TestClient(app, base_url="https://testserver") as client:
        response = login(client)
        assert "Secure" in response.headers["set-cookie"]
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]


def test_session_lifetime_follows_the_configured_duration(seeded, monkeypatch):
    import time

    from app.config import settings
    from app.db import SessionLocal
    from app.models import LoginSession

    monkeypatch.setattr(settings(), "session_duration_hours", 2)
    with TestClient(app) as client:
        response = login(client)
    assert "Max-Age=7200" in response.headers["set-cookie"]
    with SessionLocal() as db:
        session = db.query(LoginSession).one()
        assert 7100 < session.expires_at - time.time() <= 7200


def test_login_throttle_counts_failures_per_account_only(seeded):
    with TestClient(app) as client:
        # Successful logins never consume the budget, however many there are.
        for _ in range(25):
            assert login(client).status_code == 200
        # Twenty failures lock that account for that client…
        for _ in range(20):
            wrong = client.post("/api/auth/login", json={"username": "tester", "password": "wrong-password-123456"})
            assert wrong.status_code == 401
        blocked = client.post("/api/auth/login", json={"username": "tester", "password": "wrong-password-123456"})
        assert blocked.status_code == 429
        assert login(client).status_code == 429
        # …but nobody else is locked out by it.
        other = client.post("/api/auth/login", json={"username": "someone-else", "password": "wrong-password-123456"})
        assert other.status_code == 401


def test_successful_login_resets_the_failure_count(seeded):
    with TestClient(app) as client:
        for _ in range(3):
            for _ in range(15):
                client.post("/api/auth/login", json={"username": "tester", "password": "wrong-password-123456"})
            assert login(client).status_code == 200
