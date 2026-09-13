import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app


def logged_client():
    client = TestClient(app)
    client.__enter__()
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "test-password-123456789"}
    )
    assert response.status_code == 200, response.text
    return client


def test_project_access_secrets_manual_edit_and_export(book_bytes):
    client = logged_client()
    try:
        provider = client.post(
            "/api/providers",
            json={
                "name": "Local",
                "base_url": "http://llm.test/v1",
                "model": "any",
                "api_key": "not-a-real-secret",
            },
        )
        assert provider.status_code == 201
        assert "not-a-real-secret" not in provider.text
        assert "not-a-real-secret" not in client.get("/api/providers").text
        uploaded = client.post(
            "/api/projects", files={"file": ("test.epub", book_bytes, "application/epub+zip")}
        )
        assert uploaded.status_code == 201, uploaded.text
        pid = uploaded.json()["id"]
        assert client.get(f"/api/projects/{pid}/export/epub").status_code == 409
        segments = client.get(f"/api/projects/{pid}/segments").json()
        for segment in segments:
            units = [{"id": u["id"], "text": u["text"]} for u in segment["units"]]
            response = client.put(
                f"/api/segments/{segment['id']}", json={"revision": 0, "units": units, "validated": True}
            )
            assert response.status_code == 200, response.text
        stale = client.put(
            f"/api/segments/{segments[0]['id']}", json={"revision": 0, "units": units, "validated": False}
        )
        assert stale.status_code == 409
        exported = client.get(f"/api/projects/{pid}/export/epub")
        assert exported.status_code == 200, exported.text[:100]
        assert zipfile.is_zipfile(io.BytesIO(exported.content))
        user = client.post("/api/users", json={"username": "reader", "password": "reader-password-12345"})
        assert user.status_code == 201
        client.post("/api/auth/logout")
        assert (
            client.post(
                "/api/auth/login", json={"username": "reader", "password": "reader-password-12345"}
            ).status_code
            == 200
        )
        assert client.get("/api/projects").json() == []
        for path in [
            f"/api/projects/{pid}",
            f"/api/projects/{pid}/export/project",
            f"/api/segments/{segments[0]['id']}/requests",
        ]:
            assert client.get(path).status_code == 404
    finally:
        client.__exit__(None, None, None)


def test_csrf_origin_rejected():
    with TestClient(app) as client:
        result = client.post(
            "/api/auth/login",
            headers={"Origin": "https://attacker.test"},
            json={"username": "admin", "password": "test-password-123456789"},
        )
        assert result.status_code == 403


def test_export_reimport_restores_translations(book_bytes):
    client = logged_client()
    try:
        pid = client.post("/api/projects", files={"file": ("book.epub", book_bytes)}).json()["id"]
        current = client.get(f"/api/projects/{pid}").json()
        config = {
            key: current[key]
            for key in (
                "title",
                "author",
                "source_language",
                "target_language",
                "provider_id",
                "quality",
                "context_backend",
                "instructions",
            )
        }
        config.update(series_name="The Silver Tower", volume_number=2)
        assert client.put(f"/api/projects/{pid}", json=config).status_code == 200
        segment = client.get(f"/api/projects/{pid}/segments").json()[0]
        units = [{"id": u["id"], "text": u["text"]} for u in segment["units"]]
        client.put(f"/api/segments/{segment['id']}", json={"revision": 0, "units": units, "validated": True})
        exported = client.get(f"/api/projects/{pid}/export/project")
        imported = client.post("/api/projects/import", files={"file": ("project.zip", exported.content)})
        assert imported.status_code == 201, imported.text
        new_id = imported.json()["id"]
        assert new_id != pid
        restored_project = client.get(f"/api/projects/{new_id}").json()
        assert restored_project["series_name"] == "The Silver Tower"
        assert restored_project["volume_number"] == 2
        restored = client.get(f"/api/projects/{new_id}/segments").json()[0]
        assert restored["translated_units"] == units
        assert restored["validated"]
    finally:
        client.__exit__(None, None, None)
