"""Automation API: tokens and scopes, strict payloads, idempotence, queueing, results and limits."""

import asyncio
import hashlib
import io
import json
import time
import zipfile

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_pipeline import mock_completion

from app.api.tokens import limiter
from app.config import settings
from app.db import SessionLocal
from app.jobs.queue import claim
from app.jobs.requests import dispatch
from app.jobs.worker import execute, request_dispatcher
from app.main import app
from app.models import (
    ApiToken,
    AuditEntry,
    Chapter,
    Job,
    Project,
    Provider,
    Series,
    SourceAsset,
    TranslationRequest,
    User,
)
from app.security import password_hash

PASSWORD = "test-password-123456789"
ALL = ["series:read", "content:write", "pipeline:start", "jobs:read", "jobs:control", "results:read"]


@pytest.fixture
def provider_id():
    with SessionLocal() as db:
        for name in ("owner", "other"):
            db.add(User(username=name, password_hash=password_hash(PASSWORD), admin=name == "owner"))
        provider = Provider(
            name="Mock", base_url="https://llm.test/v1", model="test-model",
            capabilities={"supports_json_schema": True}, context_window=64000,
        )  # fmt: skip
        db.add(provider)
        db.commit()
        limiter.clear()
        return provider.id


def session(username: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    assert client.post("/api/auth/login", json={"username": username, "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def owner(provider_id):
    client = session("owner")
    yield client
    client.__exit__(None, None, None)


@pytest.fixture
def api():
    """A server-to-server client: no cookie, no Origin header."""
    with TestClient(app) as client:
        yield client


def new_token(client: TestClient, scopes=ALL, **extra) -> str:
    response = client.post("/api/tokens", json={"name": "CI", "scopes": scopes, **extra})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def bearer(secret: str, **headers) -> dict:
    return {"Authorization": f"Bearer {secret}", **headers}


def payload(provider_id: str | None = None, **changes) -> dict:
    body = {
        "external_id": "saga-volume-1",
        "series": {"id": None, "name": "Synthetic Saga", "create_if_missing": True},
        "volume": {"external_id": "volume-1", "number": 1, "title": "Volume 1"},
        "author": "Test Author",
        "source_language": "en",
        "target_language": "fr",
        "chapters": [
            {"external_id": "chapter-002", "number": 2, "title": "Chapter 2", "content": "Alice entered the Silver Tower and stopped.\n\nBob waited.\n"},
            {"external_id": "chapter-001", "number": 1, "title": "Chapter 1", "content": "Élodie opened the gate.\n\n* * *\n\nThe Silver Tower was silent.\n"},
        ],
        "pipeline": {"start": True, "provider_id": provider_id, "quality": "fast", "context_backend": "internal", "final_review": True},
        "output": {"format": "json"},
    }  # fmt: skip
    body.update(changes)
    return body


def counts() -> tuple[int, int, int, int]:
    with SessionLocal() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (Series, Project, Chapter, TranslationRequest)
        )


def test_tokens_are_shown_once_hashed_scoped_and_revocable(owner, api):
    created = owner.post("/api/tokens", json={"name": "Nightly", "scopes": ["jobs:read", "series:read"]})
    assert created.status_code == 201
    token = created.json()
    secret = token["token"]
    assert secret.startswith(token["prefix"] + "_") and token["prefix"].startswith("lbr_") and len(secret) > 50
    listed = owner.get("/api/tokens").json()
    assert "token" not in listed[0] and secret not in json.dumps(listed)
    assert listed[0]["scopes"] == ["series:read", "jobs:read"] and listed[0]["state"] == "active"
    with SessionLocal() as db:
        row = db.get(ApiToken, token["id"])
        assert row.token_hash == hashlib.sha256(secret.encode()).hexdigest()
        audits = list(db.scalars(select(AuditEntry)))
        assert [a.action for a in audits] == ["api_token_created"] and secret not in json.dumps(audits[0].detail)

    assert api.get("/api/v1/series", headers=bearer(secret)).status_code == 200
    with SessionLocal() as db:
        assert db.get(ApiToken, token["id"]).last_used_at
    # The session cookie opens neither the automation API nor does a token open the interface's API.
    refused = owner.get("/api/v1/series")
    assert refused.status_code == 401 and refused.json()["detail"]["code"] == "missing_token"
    assert refused.headers["www-authenticate"].startswith("Bearer")
    assert api.get("/api/projects", headers=bearer(secret)).status_code == 401
    unknown = api.get("/api/v1/series", headers=bearer(secret[:-2] + "xx"))
    assert unknown.status_code == 401 and unknown.json()["detail"]["code"] == "invalid_token"
    scope = api.post("/api/v1/translation-requests", headers=bearer(secret), json=payload())
    assert scope.status_code == 403 and scope.json()["detail"]["code"] == "insufficient_scope"
    english = api.get("/api/v1/series", headers=bearer("lbr_nothing", **{"Accept-Language": "en"}))
    assert english.json()["detail"]["message"] == "Unknown API token."

    with SessionLocal() as db:
        db.get(ApiToken, token["id"]).expires_at = time.time() - 1
        db.commit()
    assert api.get("/api/v1/series", headers=bearer(secret)).json()["detail"]["code"] == "expired_token"
    assert owner.get("/api/tokens").json()[0]["state"] == "expired"
    with SessionLocal() as db:
        db.get(ApiToken, token["id"]).expires_at = None
        db.scalar(select(User).where(User.username == "owner")).active = False
        db.commit()
    assert api.get("/api/v1/series", headers=bearer(secret)).json()["detail"]["code"] == "inactive_account"
    with SessionLocal() as db:
        db.scalar(select(User).where(User.username == "owner")).active = True
        db.commit()
    other = session("other")
    assert other.delete(f"/api/tokens/{token['id']}").status_code == 404
    assert other.get("/api/tokens").json() == []
    other.__exit__(None, None, None)
    revoked = owner.delete(f"/api/tokens/{token['id']}")
    assert revoked.json()["state"] == "revoked" and "token" not in revoked.json()
    assert api.get("/api/v1/series", headers=bearer(secret)).json()["detail"]["code"] == "revoked_token"
    with SessionLocal() as db:
        assert [a.action for a in db.scalars(select(AuditEntry).order_by(AuditEntry.created_at))] == [
            "api_token_created", "api_token_revoked",
        ]  # fmt: skip


def test_tokens_expire_after_the_chosen_days(owner):
    token = owner.post("/api/tokens", json={"name": "Short", "scopes": ["jobs:read"], "expires_in_days": 7}).json()
    assert 6.9 * 86400 < token["expires_at"] - time.time() <= 7 * 86400
    assert owner.post("/api/tokens", json={"name": "Bad", "scopes": ["admin"]}).status_code == 422


@pytest.mark.parametrize(
    ("change", "location"),
    [
        ({"unexpected": True}, ["unexpected"]),
        ({"source_language": "english!"}, ["source_language"]),
        ({"chapters": []}, ["chapters"]),
        ({"volume": {"number": 1, "surprise": 1}}, ["volume", "surprise"]),
        ({"volume": {"title": "No number"}}, ["volume", "number"]),
        ({"series": {"id": None, "name": " "}}, ["series"]),
        ({"chapters": [{"number": 1, "content": "   "}]}, ["chapters", 0, "content"]),
        ({"chapters": [{"number": 1, "content": "One."}, {"number": 1, "content": "Two."}]}, []),
        ({"chapters": [{"external_id": "a", "number": 1, "content": "One."}, {"external_id": "a", "number": 2, "content": "Two."}]}, []),
        ({"chapters": [{"number": 1, "content": "See https://example.test", "url": "https://example.test/c.txt"}]}, ["chapters", 0, "url"]),
    ],
)  # fmt: skip
def test_invalid_payloads_are_refused_without_echoing_them(owner, api, provider_id, change, location):
    secret = new_token(owner)
    response = api.post("/api/v1/translation-requests", headers=bearer(secret), json=payload(provider_id, **change))
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "invalid_payload" and detail["errors"]
    assert location in [error["loc"] for error in detail["errors"]]
    assert all("input" not in error for error in detail["errors"])
    assert counts() == (0, 0, 0, 0)


def test_language_tags_follow_bcp47(owner, api, provider_id):
    secret = new_token(owner)
    for tag in ("fr-FR", "zh-Hant", "es-419", "zh-Hant-TW"):
        body = payload(provider_id, source_language=tag, external_id=f"lang-{tag}", pipeline={"start": False})
        body["volume"] = {"number": len(tag), "external_id": f"v-{tag}"}
        assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=body).status_code == 202, tag
    duplicate = payload(provider_id, chapters=[{"number": 1, "content": "One."}, {"number": 1, "content": "Two."}])
    english = api.post(
        "/api/v1/translation-requests", headers=bearer(secret, **{"Accept-Language": "en"}), json=duplicate
    ).json()["detail"]
    assert english["message"] == "Invalid translation request."
    assert english["errors"][0]["msg"] == "Chapter 1 appears more than once in the request."


def test_limits_on_chapters_series_and_providers(owner, api, provider_id, monkeypatch):
    secret = new_token(owner)
    monkeypatch.setattr(settings(), "api_max_chapters", 1)
    too_many = api.post("/api/v1/translation-requests", headers=bearer(secret), json=payload(provider_id))
    assert too_many.status_code == 422 and "1 au maximum" in too_many.json()["detail"]["errors"][0]["msg"]
    monkeypatch.setattr(settings(), "api_max_chapters", 2000)
    monkeypatch.setattr(settings(), "text_chapter_max_chars", 1000)
    long = payload(provider_id, chapters=[{"number": 1, "content": "Word " * 400}])
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=long).status_code == 422
    missing = payload(provider_id, series={"name": "Unknown", "create_if_missing": False})
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=missing).json()["detail"]["code"] == "series_not_found"
    unknown = payload("no-such-provider")
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=unknown).json()["detail"]["code"] == "unknown_provider"
    without = payload(None)
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=without).json()["detail"]["code"] == "provider_required"
    other = session("other")
    theirs = other.post("/api/series", json={"name": "Their Saga", "kind": "books"}).json()
    other.__exit__(None, None, None)
    foreign = payload(provider_id, series={"id": theirs["id"]})
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=foreign).status_code == 404
    assert counts() == (1, 0, 0, 0)  # only the other account's series


def test_idempotency_by_key_and_external_id(owner, api, provider_id):
    secret = new_token(owner)
    headers = bearer(secret, **{"Idempotency-Key": "run-42"})
    first = api.post("/api/v1/translation-requests", headers=headers, json=payload(provider_id))
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["status"] == "pending" and body["job_id"]
    assert first.headers["location"] == body["status_url"] == f"/api/v1/translation-requests/{body['request_id']}"
    assert counts() == (1, 1, 2, 1)
    again = api.post("/api/v1/translation-requests", headers=headers, json=payload(provider_id))
    assert again.status_code == 200 and again.json() == body and again.headers["idempotent-replayed"] == "true"
    # Same content as a .json file: the same resource.
    upload = api.post(
        "/api/v1/translation-requests", headers=bearer(secret),
        files={"file": ("request.json", json.dumps(payload(provider_id)).encode(), "application/json")},
    )  # fmt: skip
    assert upload.status_code == 200 and upload.json()["request_id"] == body["request_id"]
    assert counts() == (1, 1, 2, 1)
    changed = payload(provider_id, author="Someone Else")
    conflict = api.post("/api/v1/translation-requests", headers=headers, json=changed)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "idempotency_conflict"
    by_id = api.post("/api/v1/translation-requests", headers=bearer(secret), json=changed)
    assert by_id.status_code == 409 and by_id.json()["detail"]["code"] == "idempotency_conflict"
    wrong_file = api.post(
        "/api/v1/translation-requests", headers=bearer(secret), files={"file": ("request.txt", b"{}", "text/plain")}
    )
    assert wrong_file.status_code == 422
    assert counts() == (1, 1, 2, 1)
    with SessionLocal() as db:
        asset = db.scalar(select(SourceAsset))
        assert asset.format == "json" and asset.storage_path.startswith("sources/")
        chapters = list(db.scalars(select(Chapter).order_by(Chapter.position)))
        assert [c.external_id for c in chapters] == ["chapter-001", "chapter-002"]
        assert {c.source_asset_id for c in chapters} == {asset.id}
        project = db.scalar(select(Project))
        assert (project.source_format, project.project_kind, project.external_id) == ("json", "volume", "volume-1")
        assert (project.volume_number, project.target_language, project.quality) == (1, "fr", "fast")


def test_changed_chapters_conflict_unless_replacement_is_asked(owner, api, provider_id):
    secret = new_token(owner)
    first = payload(provider_id, pipeline={"start": False})
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=first).status_code == 202
    edited = payload(provider_id, external_id="saga-volume-1-bis", pipeline={"start": False})
    edited["chapters"][0]["content"] = "Alice left the Silver Tower.\n"
    conflict = api.post("/api/v1/translation-requests", headers=bearer(secret), json=edited)
    assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "chapter_conflict"
    assert conflict.json()["detail"]["conflicts"]
    edited["replace_changed_chapters"] = True
    replaced = api.post("/api/v1/translation-requests", headers=bearer(secret), json=edited)
    assert replaced.status_code == 202 and replaced.json()["status"] == "imported"
    status = api.get(replaced.json()["status_url"], headers=bearer(secret)).json()
    assert (status["chapters"]["replaced"], status["chapters"]["unchanged"]) == (1, 1)
    assert counts() == (1, 1, 2, 2)


def test_owners_never_see_each_other(owner, api, provider_id):
    mine = new_token(owner)
    created = api.post("/api/v1/translation-requests", headers=bearer(mine), json=payload(provider_id)).json()
    other = session("other")
    theirs = new_token(other)
    other.__exit__(None, None, None)
    for path in (created["status_url"], created["result_url"] + "?partial=true", f"/api/v1/series/{created['series_id']}"):
        assert api.get(path, headers=bearer(theirs)).status_code == 404, path
    assert api.post(created["status_url"] + "/cancel", headers=bearer(theirs)).status_code == 404
    assert api.get("/api/v1/series", headers=bearer(theirs)).json() == []
    listed = api.get("/api/v1/series", headers=bearer(mine)).json()
    assert [s["name"] for s in listed] == ["Synthetic Saga"] and listed[0]["volumes"] == 1
    detail = api.get(f"/api/v1/series/{created['series_id']}", headers=bearer(mine)).json()
    assert detail["volume_list"][0]["external_id"] == "volume-1" and detail["volume_list"][0]["chapters"] == 2


def test_pause_resume_and_cancel(owner, api, provider_id):
    secret = new_token(owner)
    created = api.post("/api/v1/translation-requests", headers=bearer(secret), json=payload(provider_id)).json()
    url = created["status_url"]
    assert api.post(url + "/pause", headers=bearer(secret)).json()["status"] == "paused"
    assert api.post(url + "/resume", headers=bearer(secret)).json()["status"] == "pending"
    assert api.post(url + "/resume", headers=bearer(secret)).status_code == 409
    assert api.post(url + "/cancel", headers=bearer(secret)).json()["status"] == "cancelled"
    reader = new_token(owner, ["jobs:read"])
    assert api.post(url + "/resume", headers=bearer(reader)).status_code == 403


def test_rate_limit_per_token(owner, api, monkeypatch):
    secret = new_token(owner)
    monkeypatch.setattr(settings(), "api_rate_limit_per_minute", 2)
    assert [api.get("/api/v1/series", headers=bearer(secret)).status_code for _ in range(2)] == [200, 200]
    limited = api.get("/api/v1/series", headers=bearer(secret))
    assert limited.status_code == 429 and 1 <= int(limited.headers["retry-after"]) <= 61
    assert limited.json()["detail"]["code"] == "rate_limited"
    assert api.get("/api/v1/series", headers=bearer(new_token(owner))).status_code == 200


def test_body_size_is_bounded_before_reading(owner, api, provider_id, monkeypatch):
    secret = new_token(owner)
    monkeypatch.setattr(settings(), "api_max_payload_mb", 1)
    # Declared too large: refused by the middleware before anything is read.
    refused = api.post("/api/v1/translation-requests", headers=bearer(secret, **{"Content-Type": "application/json"}),
                       content=b"x" * (2 * 1024**2 + 10))  # fmt: skip
    assert refused.status_code == 413 and refused.json()["detail"]["code"] == "payload_too_large"
    anonymous = api.post("/api/v1/translation-requests", content=b"x" * (1024**2 + 10),
                         headers={"Content-Type": "application/json"})  # fmt: skip
    assert anonymous.status_code == 401
    big = payload(provider_id, chapters=[{"number": 1, "content": "A short synthetic sentence here. " * 36_000}])
    big["pipeline"] = {"start": False}
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=big).status_code == 413
    # Above the anonymous megabyte, within the token's limit: accepted.
    monkeypatch.setattr(settings(), "api_max_payload_mb", 2)
    accepted = api.post("/api/v1/translation-requests", headers=bearer(secret), json=big)
    assert accepted.status_code == 202, accepted.text[:300]


def test_origin_less_server_clients_are_accepted_but_cross_site_browsers_are_not(owner, api, provider_id):
    secret = new_token(owner)
    body = payload(provider_id, pipeline={"start": False})
    cross = api.post("/api/v1/translation-requests", headers=bearer(secret, **{"Sec-Fetch-Site": "cross-site"}), json=body)
    assert cross.status_code == 403
    assert api.post("/api/v1/translation-requests", headers=bearer(secret), json=body).status_code == 202


async def run_pending_job() -> Job:
    claimed = claim()
    assert claimed
    await execute(*claimed)
    with SessionLocal() as db:
        return db.get(Job, claimed[0])


@respx.mock
async def test_queued_request_starts_after_the_volume_is_free_even_after_a_restart(owner, api, provider_id):
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    secret = new_token(owner)
    first = api.post("/api/v1/translation-requests", headers=bearer(secret), json=payload(provider_id)).json()
    later = payload(provider_id, external_id="saga-volume-1-part-2")
    later["chapters"] = [{"external_id": "chapter-003", "number": 3, "title": "Chapter 3", "content": "Bob climbed the Silver Tower.\n"}]
    queued = api.post("/api/v1/translation-requests", headers=bearer(secret), json=later)
    assert queued.status_code == 202 and queued.json()["status"] == "queued" and queued.json()["job_id"] is None
    assert dispatch() == 0  # the first job still holds the volume
    status = api.get(queued.json()["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "queued" and status["chapters"]["items"] == []
    finished = await run_pending_job()
    assert finished.status == "completed", finished.error

    # The API and the worker restart: a fresh dispatcher only reads SQL.
    stopped = asyncio.Event()
    loop = asyncio.create_task(request_dispatcher(stopped, interval=0.05))
    for _ in range(100):
        await asyncio.sleep(0.05)
        with SessionLocal() as db:
            if db.get(TranslationRequest, queued.json()["request_id"]).job_id:
                break
    stopped.set()
    await loop
    status = api.get(queued.json()["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "pending" and status["job_id"]
    assert [c["external_id"] for c in status["chapters"]["items"]] == ["chapter-003"]
    job = await run_pending_job()
    assert job.status == "completed", job.error
    assert job.options["translation_request"] == status["request_id"]
    dispatch()
    with SessionLocal() as db:
        assert {r.status for r in db.scalars(select(TranslationRequest))} == {"completed"}
    result = api.get(queued.json()["result_url"], headers=bearer(secret)).json()
    assert [c["external_id"] for c in result["chapters"]] == ["chapter-003"] and result["complete"]
    assert api.get(first["result_url"], headers=bearer(secret)).json()["complete"]


@respx.mock
async def test_full_request_to_json_txt_and_zip_results(owner, api, provider_id):
    respx.post("https://llm.test/v1/chat/completions").mock(side_effect=mock_completion)
    secret = new_token(owner)
    body = payload(provider_id)
    body["pipeline"]["final_review"] = False
    created = api.post("/api/v1/translation-requests", headers=bearer(secret), json=body).json()
    early = api.get(created["result_url"], headers=bearer(secret))
    assert early.status_code == 409 and early.json()["detail"]["code"] == "result_not_ready"
    assert early.json()["detail"]["incomplete_chapters"] == ["chapter-001", "chapter-002"]
    partial = api.get(created["result_url"] + "?partial=true", headers=bearer(secret))
    assert partial.status_code == 200 and partial.json()["complete"] is False
    assert partial.headers["x-libris-complete"] == "false"

    job = await run_pending_job()
    assert job.status == "completed", job.error
    assert job.options["final_review"] is False and "review_targets" not in job.checkpoint
    status = api.get(created["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "completed" and status["progress"]["percent"] == 100
    assert status["progress"]["stages"][0]["key"] == "import"

    result = api.get(created["result_url"], headers=bearer(secret)).json()
    assert result["complete"] and result["schema_version"] == 1
    assert result["volume"] == {"project_id": created["project_id"], "external_id": "volume-1", "number": 1, "title": "Volume 1"}
    assert result["series"]["name"] == "Synthetic Saga"
    assert result["strategy"] == {
        "provider": {"name": "Mock", "model": "test-model"}, "quality": "fast", "context_backend": "internal",
        "final_review": False,
    }  # fmt: skip
    assert "llm.test" not in json.dumps(result)
    first, second = result["chapters"]
    assert (first["external_id"], first["number"], second["external_id"]) == ("chapter-001", 1, "chapter-002")
    assert first["translated_title"] == "Chapitre 1"
    assert "Tour d’argent" in first["translation"] and "* * *" in first["translation"]
    assert first["sha256"] == hashlib.sha256(first["translation"].encode()).hexdigest()
    assert first["source_sha256"] == hashlib.sha256(body["chapters"][1]["content"].encode()).hexdigest()
    assert first["review"]["segments"] >= 1 and first["issues"] == [] and first["flagged_passages"] == []

    text = api.get(created["result_url"] + "?format=txt", headers=bearer(secret))
    assert text.headers["content-type"] == "text/plain; charset=utf-8"
    assert text.content.decode("utf-8").index("Chapitre 1") < text.content.decode("utf-8").index("Chapitre 2")
    archive = api.get(created["result_url"] + "?format=txt-zip", headers=bearer(secret))
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        names = bundle.namelist()
        assert names == ["chapters/001 - Chapitre 1.txt", "chapters/002 - Chapitre 2.txt", "manifest.json"]
        manifest = json.loads(bundle.read("manifest.json"))
        for entry in manifest["chapters"]:
            data = bundle.read(entry["file"])
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
            data.decode("utf-8")
        assert "Élodie" in bundle.read(names[0]).decode("utf-8")
        assert manifest["complete"] and [c["external_id"] for c in manifest["chapters"]] == ["chapter-001", "chapter-002"]
