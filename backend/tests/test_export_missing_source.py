from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app, login_attempts
from app.models import Chapter, Project, Segment


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    login_attempts.clear()
    yield
    login_attempts.clear()


@pytest.fixture
def translated(seeded):
    pid = seeded[0]
    with SessionLocal() as db:
        for segment in db.scalars(select(Segment).where(Segment.project_id == pid)):
            segment.translated_units = [{"id": u["id"], "text": u["text"]} for u in segment.units]
            segment.translation = segment.source
            segment.stage, segment.status = "done", "ok"
        db.commit()
        path = Path(db.get(Project, pid).original_path)
        chapter = db.scalar(select(Chapter.id).where(Chapter.project_id == pid))
    return pid, path, chapter


def client():
    api = TestClient(app)
    assert api.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}).status_code == 200
    return api


def test_text_exports_survive_a_missing_source_file(translated):
    pid, path, chapter = translated
    path.unlink()
    api = client()
    assert api.get(f"/api/projects/{pid}/export/txt").status_code == 200
    assert api.get(f"/api/projects/{pid}/export/md").status_code == 200
    assert api.get(f"/api/projects/{pid}/export/bible").status_code == 200
    for url in (f"/api/projects/{pid}/export/epub", f"/api/projects/{pid}/export/project", f"/api/projects/{pid}/preview/{chapter}"):
        response = api.get(url)
        assert response.status_code == 409 and "introuvable" in response.json()["detail"]


def test_a_relocated_data_directory_is_found(translated, tmp_path, monkeypatch):
    pid, path, _ = translated
    books = tmp_path / "books"
    books.mkdir()
    path.rename(books / f"{pid}.epub")
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    assert client().get(f"/api/projects/{pid}/export/project").status_code == 200
