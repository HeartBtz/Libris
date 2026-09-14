import io
import zipfile

from fastapi.testclient import TestClient

from app.main import app


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "tester", "password": "test-password-123456789"},
    )
    assert response.status_code == 200


def complete_translation(client, project_id):
    for segment in client.get(f"/api/projects/{project_id}/segments").json():
        response = client.put(
            f"/api/segments/{segment['id']}",
            json={
                "revision": 0,
                "units": [{"id": unit["id"], "text": unit["text"]} for unit in segment["units"]],
                "validated": True,
            },
        )
        assert response.status_code == 200


def test_batch_epub_export_is_complete_and_uses_unique_safe_names(seeded, book_bytes):
    first_id, _, _ = seeded
    with TestClient(app) as client:
        login(client)
        second_book = io.BytesIO(book_bytes)
        with zipfile.ZipFile(second_book, "a") as archive:
            archive.comment = b"second test copy"
        second = client.post(
            "/api/projects",
            files={"file": ("second.epub", second_book.getvalue(), "application/epub+zip")},
        )
        assert second.status_code == 201
        second_id = second.json()["id"]
        complete_translation(client, first_id)

        blocked = client.post(
            "/api/exports/epub", json={"project_ids": [first_id, second_id]}
        )
        assert blocked.status_code == 409
        assert "The Silver Tower" in blocked.json()["detail"]

        complete_translation(client, second_id)
        exported = client.post(
            "/api/exports/epub", json={"project_ids": [first_id, second_id]}
        )
        assert exported.status_code == 200
        assert exported.headers["content-type"] == "application/zip"
        assert 'filename="libris-epubs.zip"' in exported.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert archive.namelist() == ["The Silver Tower.epub", "The Silver Tower (2).epub"]
            assert all(zipfile.is_zipfile(io.BytesIO(archive.read(name))) for name in archive.namelist())


def test_batch_epub_export_rejects_duplicate_project_ids(seeded):
    project_id, _, _ = seeded
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/exports/epub", json={"project_ids": [project_id, project_id]}
        )
    assert response.status_code == 422
    assert "double" in response.json()["detail"]
