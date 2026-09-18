import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, login_attempts


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    login_attempts.clear()
    yield
    login_attempts.clear()


def login(client):
    response = client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
    assert response.status_code == 200


MULTIPART = {"content-type": "multipart/form-data; boundary=frontier"}


def chunks(size: int):
    yield b'--frontier\r\nContent-Disposition: form-data; name="file"; filename="big.epub"\r\n\r\n'
    sent = 0
    while sent < size:
        yield b"x" * 65536
        sent += 65536
    yield b"\r\n--frontier--\r\n"


def test_anonymous_upload_is_refused_without_reading_it(seeded, monkeypatch):
    import app.limits as limits

    seen = []
    original = limits.BodyLimit.__call__

    async def spy(self, scope, receive, send):
        async def counting():
            message = await receive()
            seen.append(len(message.get("body", b"")))
            return message

        await original(self, scope, counting, send)

    monkeypatch.setattr(limits.BodyLimit, "__call__", spy)
    with TestClient(app) as client:
        response = client.post("/api/projects", files={"file": ("big.epub", b"x" * (3 * 1024**2))})
    assert response.status_code == 401
    assert sum(seen) == 0  # rejected on Content-Length alone


def test_anonymous_chunked_body_is_cut_off(seeded):
    with TestClient(app) as client:
        response = client.post("/api/projects", content=chunks(3 * 1024**2), headers=MULTIPART)
    assert response.status_code == 401


def test_authenticated_upload_over_the_limit_is_413(seeded, monkeypatch):
    monkeypatch.setattr(settings(), "max_upload_mb", 1)
    with TestClient(app) as client:
        login(client)
        too_big = client.post("/api/projects", files={"file": ("big.epub", b"x" * (3 * 1024**2))})
        assert too_big.status_code == 413 and "1 Mo" in too_big.json()["detail"]
        streamed = client.post("/api/projects", content=chunks(3 * 1024**2), headers=MULTIPART)
        assert streamed.status_code == 413


def test_a_normal_upload_still_works(seeded, book_bytes):
    with TestClient(app) as client:
        login(client)
        response = client.post("/api/projects", files={"file": ("again.epub", book_bytes, "application/epub+zip")})
    assert response.status_code in (201, 409)  # 409: the seeded fixture already imported this book
