import io
import zipfile

import httpx
import respx
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Provider, RequestLog


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


def test_model_statistics_aggregate_registered_models(seeded):
    pid, _, provider_id = seeded
    with SessionLocal() as db:
        db.add(
            RequestLog(
                project_id=pid,
                provider_id=provider_id,
                operation="translation",
                model="test-model",
                fingerprint="test-fingerprint",
                parameters={},
                messages=[],
                prompt_tokens=120,
                completion_tokens=80,
            )
        )
        db.add(Provider(name="Idle", base_url="https://idle.test", model="idle-model"))
        db.commit()

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login", json={"username": "tester", "password": "test-password-123456789"}
        )
        assert login.status_code == 200, login.text
        response = client.get("/api/statistics/models")

    assert response.status_code == 200, response.text
    models = {item["model"]: item for item in response.json()}
    assert models["test-model"] == {
        "model": "test-model",
        "requests": 1,
        "input_tokens": 120,
        "output_tokens": 80,
        "total_tokens": 200,
        "wasted_input_tokens": 0,
        "wasted_share": 0,
    }
    assert models["idle-model"]["total_tokens"] == 0


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
        assert client.delete(f"/api/projects/{pid}").status_code == 200
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


def test_unexpected_error_is_traceable_without_leaking_its_message(seeded, monkeypatch, caplog):
    def explode(*_arguments):
        raise RuntimeError("private sentence from a book")

    monkeypatch.setattr("app.api.projects.project_view", explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        response = client.get(f"/api/projects/{seeded[0]}")
    assert response.status_code == 500
    reference = response.json()["detail"].split("diagnostic : ")[1].split(".")[0]
    assert f"reference={reference}" in caplog.text
    assert "trace=RuntimeError" in caplog.text and "projects.py" in caplog.text
    assert "private sentence" not in caplog.text and "private sentence" not in response.text


def test_provider_deletion_explains_what_still_references_it(seeded):
    pid, _, provider_id = seeded
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        used = client.delete(f"/api/providers/{provider_id}")
        assert used.status_code == 409 and "1 livre(s)" in used.json()["detail"]
        with SessionLocal() as db:
            from app.models import Project

            db.get(Project, pid).provider_id = None
            db.add(
                RequestLog(
                    project_id=pid, provider_id=provider_id, operation="translation", model="m",
                    fingerprint="f", status="success", messages=[], parameters={},
                )
            )
            db.commit()
        history = client.delete(f"/api/providers/{provider_id}")
        assert history.status_code == 409 and "1 requête(s)" in history.json()["detail"]
        assert "existe déjà" not in history.json()["detail"]
        unused = client.post(
            "/api/providers",
            json={"name": "Unused", "base_url": "https://unused.test/v1", "model": "m", "context_window": 32768},
        ).json()
        assert client.delete(f"/api/providers/{unused['id']}").json() == {"ok": True}


@respx.mock
def test_unreachable_external_service_is_a_502_not_a_generic_500(seeded):
    from app.models import AppSetting

    pid = seeded[0]
    with SessionLocal() as db:
        db.add(AppSetting(key="openviking", value={"base_url": "https://memory.test", "root_uri": "viking://resources/t"}))
        db.commit()
    respx.route(host="memory.test").mock(side_effect=httpx.ConnectError("refused"))
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        for method, path in (("get", "memory/documents/book.md"), ("post", "memory/reindex")):
            response = getattr(client, method)(f"/api/projects/{pid}/{path}")
            assert response.status_code == 502, response.text
            assert "injoignable (ConnectError)" in response.json()["detail"]


def stored_books():
    from app.config import settings

    return sorted(path.name for path in (settings().data_dir / "books").glob("*.epub"))


def test_failed_imports_leave_no_orphan_file(seeded, book_bytes, monkeypatch):
    import json as jsonlib

    variant = io.BytesIO(book_bytes)
    with zipfile.ZipFile(variant, "a") as archive:
        archive.comment = b"another copy"
    before = stored_books()
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})

        def explode(*_arguments, **_options):
            raise RuntimeError("commit path failure")

        monkeypatch.setattr("app.api.projects.emit", explode)
        failed = client.post("/api/projects", files={"file": ("b.epub", variant.getvalue(), "application/epub+zip")})
        assert failed.status_code == 500 and stored_books() == before
        monkeypatch.undo()

        # A project archive whose saved structure does not match its EPUB is refused after the import step.
        bundle = io.BytesIO()
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("original.epub", variant.getvalue())
            archive.writestr("project.json", jsonlib.dumps({"schema_version": 1, "project": {}, "segments": []}))
        refused = client.post("/api/projects/import", files={"file": ("p.zip", bundle.getvalue(), "application/zip")})
        assert refused.status_code == 422 and stored_books() == before


def test_import_survives_a_validator_timeout_and_bounds_metadata(seeded, monkeypatch):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("long-metadata")
    book.set_title("T" * 700)
    book.set_language("en")
    book.add_author("A" * 700)
    chapter = epub.EpubHtml(title="One", file_name="one.xhtml", lang="en")
    chapter.content = "<html><body><h1>One</h1><p>Hello there.</p></body></html>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapter]
    data = io.BytesIO()
    epub.write_epub(data, book)

    def timeout(_data):
        raise ValueError("EPUBCheck n’a pas terminé en 90 secondes ; réessayez plus tard.")

    monkeypatch.setattr("app.api.projects.epubcheck", timeout)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        created = client.post("/api/projects", files={"file": ("l.epub", data.getvalue(), "application/epub+zip")})
    assert created.status_code == 201, created.text
    body = created.json()
    assert len(body["title"]) == 500 and len(body["author"]) == 500
    assert body["book_info"]["validation"]["valid"] is None
    assert "90 secondes" in body["book_info"]["validation"]["message"]


def test_resolving_the_last_issue_clears_the_segment_flag(seeded):
    from sqlalchemy import select

    from app.models import Issue, Segment

    pid = seeded[0]
    with SessionLocal() as db:
        first, second = list(db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)))[:2]
        for segment in (first, second):
            segment.translation, segment.status = "Traduit.", "check"
        second.critique = [{"unit_id": "u", "suggestion": "Encore à traiter."}]
        issues = [
            Issue(project_id=pid, segment_id=first.id, severity="warning", code="length", message="a"),
            Issue(project_id=pid, segment_id=first.id, severity="warning", code="terms", message="b"),
            Issue(project_id=pid, segment_id=second.id, severity="warning", code="length", message="c"),
        ]
        db.add_all(issues)
        db.commit()
        ids, first_id, second_id = [issue.id for issue in issues], first.id, second.id
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "tester", "password": "test-password-123456789"})
        assert client.get(f"/api/projects/{pid}").json()["stats"]["flagged"] == 2
        assert client.post(f"/api/issues/{ids[0]}/resolve").status_code == 200
        with SessionLocal() as db:
            assert db.get(Segment, first_id).status == "check"  # one issue left
        client.post(f"/api/issues/{ids[1]}/resolve")
        client.post(f"/api/issues/{ids[2]}/resolve")
        with SessionLocal() as db:
            assert db.get(Segment, first_id).status == "ok"
            assert db.get(Segment, second_id).status == "check"  # a critique is still pending
        assert client.get(f"/api/projects/{pid}").json()["stats"]["flagged"] == 1
