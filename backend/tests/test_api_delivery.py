"""Automation API delivery: EPUB and TXT uploads in, translated results out, with no human step.

Every journey here uses only the API (a Bearer token) and the mock LLM: upload, wait, download. The
request always ends (completed, completed_with_residuals, failed or cancelled) with a report."""

import hashlib
import hmac
import io
import json
import time
import zipfile

import httpx
import pytest
import respx
import test_api_v1
from sqlalchemy import select
from test_api_v1 import bearer, new_token, payload, run_pending_job
from test_epubcheck_exports import assert_valid, with_real_image
from test_pipeline import mock_completion

from app.config import settings
from app.db import SessionLocal
from app.engines.delivery import report as report_module
from app.engines.delivery import webhooks
from app.engines.ingestion.store import data_path
from app.jobs.requests import dispatch
from app.models import Chapter, Job, Segment, TranslationRequest

# The automation API's own fixtures: a provider, the owner's session and a cookie-less client.
provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api
LLM = "https://llm.test/v1/chat/completions"
REQUESTS = "/api/v1/translation-requests"


def epub_fields(provider: str, **extra) -> dict:
    return {
        "series": "Silver Saga", "volume": "1", "source_language": "en", "target_language": "fr",
        "provider_id": provider, "quality": "fast", "final_review": "false", **extra,
    }  # fmt: skip


def upload_epub(client, secret: str, data: bytes, provider: str, headers: dict | None = None, **fields):
    return client.post(
        REQUESTS,
        headers=bearer(secret, **(headers or {})),
        files={"file": ("The Silver Tower.epub", data, "application/epub+zip")},
        data=epub_fields(provider, **fields),
    )


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def chapter_file(project_id: str, title: str) -> str:
    with SessionLocal() as db:
        return db.scalar(
            select(Chapter.resource).where(Chapter.project_id == project_id, Chapter.title == title)
        )


@respx.mock
async def test_an_epub_goes_in_and_a_translated_epub_comes_out(owner, api, provider_id, book_bytes):
    respx.post(LLM).mock(side_effect=mock_completion)
    secret = new_token(owner)
    created = upload_epub(api, secret, book_bytes, provider_id, headers={"Idempotency-Key": "silver-1"})
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["input"] == "epub" and body["status"] == "pending" and body["job_id"]
    again = upload_epub(api, secret, book_bytes, provider_id, headers={"Idempotency-Key": "silver-1"})
    assert again.status_code == 200 and again.json()["request_id"] == body["request_id"]
    assert again.headers["idempotent-replayed"] == "true"
    other = upload_epub(
        api, secret, book_bytes, provider_id, headers={"Idempotency-Key": "silver-1"}, volume="2"
    )
    assert other.status_code == 409 and other.json()["detail"]["code"] == "idempotency_conflict"

    job = await run_pending_job()
    assert job.status == "completed", job.error
    result = api.get(body["result_url"] + "?wait=5", headers=bearer(secret))
    assert result.status_code == 200, result.text
    assert result.headers["content-type"] == "application/epub+zip"
    assert result.headers["x-libris-status"] == "completed" and result.headers["x-libris-complete"] == "true"
    chapter = entries(result.content)[chapter_file(body["project_id"], "Chapter One")].decode()
    assert "Tour d’argent" in chapter and "Chapitre One" in chapter

    status = api.get(body["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "completed" and status["finished_at"]
    report = status["report"]
    assert report["outcome"] == "completed" and report["residual_total"] == 0 and report["residuals"] == []
    assert report["passages"]["total"] == report["passages"]["translated"] > 0
    assert report["usage"]["calls"] > 0 and report["usage"]["prompt_tokens"] > 0
    assert report["durations"]["job_seconds"] is not None
    assert report["delivery"]["validation"]["available"] is False  # no EPUBCheck in this test run
    assert report["decisions"]["intake"] == [] and report["decisions"]["autopilot"] >= 0
    assert status["result"]["format"] == "epub"
    assert status["result"]["sha256"] == hashlib.sha256(result.content).hexdigest()
    with SessionLocal() as db:
        stored = db.get(TranslationRequest, body["request_id"]).artifact
        assert stored["path"] == f"results/{body['request_id']}/result.epub"
        assert data_path(stored["path"]).read_bytes() == result.content
    # Other formats of the same volume, negotiated by Accept or asked for explicitly.
    text = api.get(body["result_url"], headers=bearer(secret, Accept="text/plain"))
    assert text.headers["content-type"].startswith("text/plain") and "Tour d’argent" in text.text
    document = api.get(body["result_url"] + "?format=json", headers=bearer(secret)).json()
    assert document["status"] == "completed" and document["report"]["outcome"] == "completed"


@pytest.mark.epubcheck
@respx.mock
async def test_the_delivered_epub_passes_epubcheck(owner, api, provider_id, book_bytes, epubcheck_jar):
    respx.post(LLM).mock(side_effect=mock_completion)
    secret = new_token(owner)
    body = upload_epub(api, secret, with_real_image(book_bytes), provider_id).json()
    assert (await run_pending_job()).status == "completed"
    result = api.get(body["result_url"] + "?wait=5", headers=bearer(secret))
    assert result.status_code == 200, result.text
    assert_valid(result.content)
    status = api.get(body["status_url"], headers=bearer(secret)).json()
    assert status["report"]["delivery"]["validation"] == {"available": True, "valid": True}


def test_a_raw_epub_body_takes_its_options_from_the_query(owner, api, provider_id, book_bytes):
    secret = new_token(owner)
    unknown = api.post(REQUESTS + "?surprise=1", headers=bearer(secret, **{"Content-Type": "application/epub+zip"}),
                       content=book_bytes)  # fmt: skip
    assert unknown.status_code == 422 and unknown.json()["detail"]["errors"][0]["loc"] == ["surprise"]
    query = "?source_language=en&target_language=fr&start=false&filename=Tower.epub&external_id=tower-1"
    created = api.post(REQUESTS + query, headers=bearer(secret, **{"Content-Type": "application/epub+zip"}),
                       content=book_bytes)  # fmt: skip
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["status"] == "imported" and body["series_id"] is None
    # Nothing is translated yet: only a partial result, in the source text.
    early = api.get(body["result_url"], headers=bearer(secret))
    assert early.status_code == 409 and early.json()["detail"]["code"] == "result_not_ready"
    partial = api.get(body["result_url"] + "?partial=true", headers=bearer(secret))
    assert partial.status_code == 200 and partial.headers["x-libris-complete"] == "false"
    assert (
        "Alice entered the"
        in entries(partial.content)[chapter_file(body["project_id"], "Chapter One")].decode()
    )
    # The same file again reuses its volume (and says so) instead of refusing a duplicate.
    reused = api.post(REQUESTS + "?target_language=fr&start=false", content=book_bytes,
                      headers=bearer(secret, **{"Content-Type": "application/epub+zip"}))  # fmt: skip
    assert reused.status_code == 202 and reused.json()["project_id"] == body["project_id"]
    with SessionLocal() as db:
        decisions = db.get(TranslationRequest, reused.json()["request_id"]).options["decisions"]
        assert "déjà dans la bibliothèque" in decisions[0]["reason"]
    unreadable = api.post(REQUESTS + "?target_language=fr&start=false", content=b"not a zip",
                          headers=bearer(secret, **{"Content-Type": "application/epub+zip"}))  # fmt: skip
    assert unreadable.status_code == 422 and unreadable.json()["detail"]["code"] == "invalid_epub"
    starter = new_token(owner, ["content:write"])
    refused = upload_epub(api, starter, book_bytes, provider_id, volume="3")
    assert refused.status_code == 403 and refused.json()["detail"]["scope"] == "pipeline:start"


def txt(name: str, text: str) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, text.encode(), "text/plain"))


@respx.mock
async def test_txt_chapters_go_in_and_a_zip_of_chapters_comes_out(owner, api, provider_id):
    respx.post(LLM).mock(side_effect=mock_completion)
    secret = new_token(owner)
    files = [
        txt("Chapter 2.txt", "Bob waited near the Silver Tower.\n"),
        txt("Chapter 1.txt", "Alice entered the Silver Tower and stopped.\n"),
        txt("afterword.txt", "Thanks for reading.\n"),
    ]
    fields = {"series": "Web Saga", "volume": "1", "source_language": "en", "target_language": "fr",
              "provider_id": provider_id, "quality": "fast", "final_review": "false", "output_format": "txt-zip"}  # fmt: skip
    created = api.post(REQUESTS, headers=bearer(secret), files=files, data=fields)
    assert created.status_code == 202, created.text
    body = created.json()
    assert body["input"] == "txt"
    status = api.get(body["status_url"], headers=bearer(secret)).json()
    assert [c["number"] for c in status["chapters"]["items"]] == [1, 2, 3]
    with SessionLocal() as db:
        decisions = db.get(TranslationRequest, body["request_id"]).options["decisions"]
    assert decisions == [{"file": 2, "name": "afterword.txt", "chapter_number": 3.0, "confidence": "low",
                          "reason": "aucun numéro dans le nom : numéro donné par l’ordre d’envoi"}]  # fmt: skip

    assert (await run_pending_job()).status == "completed"
    result = api.get(body["result_url"] + "?wait=5", headers=bearer(secret))
    assert result.status_code == 200 and result.headers["content-type"] == "application/zip"
    files_out = entries(result.content)
    assert sorted(files_out) == [
        "chapters/001 - Chapitre 1.txt", "chapters/002 - Chapitre 2.txt", "chapters/003 - afterword.txt",
        "manifest.json", "report.json",
    ]  # fmt: skip
    assert "Tour d’argent" in files_out["chapters/001 - Chapitre 1.txt"].decode()
    assert json.loads(files_out["report.json"])["outcome"] == "completed"
    document = api.get(body["result_url"], headers=bearer(secret, Accept="application/json")).json()
    assert document["complete"] and len(document["chapters"]) == 3
    assert api.get(body["result_url"] + "?format=epub", headers=bearer(secret)).status_code == 409
    # Mixed kinds, or neither series nor volume: refused before anything is stored.
    mixed = api.post(REQUESTS, headers=bearer(secret), data=fields,
                     files=[txt("Chapter 4.txt", "x"), ("files", ("b.json", b"{}", "application/json"))])  # fmt: skip
    assert mixed.status_code == 422
    missing = api.post(REQUESTS, headers=bearer(secret), files=[txt("Chapter 4.txt", "Four.\n")],
                       data={"source_language": "en", "target_language": "fr"})  # fmt: skip
    assert missing.status_code == 422 and missing.json()["detail"]["errors"][0]["loc"] == ["volume"]


@respx.mock
async def test_residual_passages_keep_their_source_and_are_listed(owner, api, provider_id):
    respx.post(LLM).mock(side_effect=mock_completion)
    secret = new_token(owner)
    body = payload(provider_id)
    body["pipeline"]["final_review"] = False
    created = api.post(REQUESTS, headers=bearer(secret), json=body).json()
    assert (await run_pending_job()).status == "completed"
    with SessionLocal() as db:
        segments = db.scalars(
            select(Segment).where(Segment.project_id == created["project_id"]).order_by(Segment.position)
        ).all()
        kept, missing = segments[0], segments[-1]
        kept.translation, kept.retained_source, kept.status = kept.source, True, "source_retained"
        kept.error = "Refus répété du fournisseur."
        missing.translation, missing.translated_units, missing.status = "", [], "error"
        kept_id, missing_id, missing_source = kept.id, missing.id, missing.units[-1]["text"]
        db.commit()
    status = api.get(created["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "completed_with_residuals"
    report = status["report"]
    assert report["residual_total"] == 2 and report["passages"]["source_retained"] == 1
    reasons = {item["segment_id"]: item["reason"] for item in report["residuals"]}
    assert reasons == {kept_id: "Refus répété du fournisseur.", missing_id: "untranslated"}
    result = api.get(created["result_url"], headers=bearer(secret))
    assert result.status_code == 200 and result.headers["x-libris-complete"] == "false"
    document = result.json()
    assert document["status"] == "completed_with_residuals" and document["complete"] is False
    assert any(missing_source.strip() in chapter["translation"] for chapter in document["chapters"])


def test_the_autopilot_report_is_read_when_present_and_tolerated_when_absent():
    class Finished:
        created_at, finished_at, id = 0.0, 1.0, "job"
        result = {"autopilot": {"outcome": "completed_with_residuals", "rounds": 2, "reason": None,
                                "residuals": [{"segment_id": "s1", "chapter_id": "c1", "reason": "refus"}]}}  # fmt: skip

    assert report_module.autopilot_report(Finished())["rounds"] == 2
    assert report_module.autopilot_report(Job()) is None and report_module.autopilot_report(None) is None
    assert report_module.residual_reason(True, "", {"reason": "refus"}) == "refus"
    assert report_module.residual_reason(True, "", {}) == "source_retained"


def fake_check(broken: str | None, original_valid: bool = True, path: str | None = None):
    """EPUBCheck stand-in: rejects a book whose `broken` text is still there (or always, when None)."""

    def check(content: bytes) -> dict:
        found = entries(content)
        translated = any("Tour d’argent".encode() in value for value in found.values())
        invalid = (broken is None and (translated or not original_valid)) or (
            broken is not None and broken.encode() in found.get(path, b"")
        )
        if not invalid:
            return {"available": True, "valid": True, "report": {"messages": []}}
        location = [{"path": path}] if path else []
        return {"available": True, "valid": False,
                "report": {"messages": [{"ID": "RSC-005", "severity": "ERROR", "message": "Bad markup",
                                         "locations": location}]}}  # fmt: skip

    return check


async def translated_epub_request(api, owner, provider_id, book_bytes) -> tuple[str, dict]:
    secret = new_token(owner)
    body = upload_epub(api, secret, book_bytes, provider_id).json()
    assert (await run_pending_job()).status == "completed"
    return secret, body


@respx.mock
async def test_epubcheck_failures_are_repaired_by_restoring_the_named_files(owner, api, provider_id, book_bytes,
                                                                           monkeypatch):  # fmt: skip
    respx.post(LLM).mock(side_effect=mock_completion)
    secret, body = await translated_epub_request(api, owner, provider_id, book_bytes)
    path = chapter_file(body["project_id"], "Chapter One")
    monkeypatch.setattr("app.engines.delivery.epub.epubcheck", fake_check("Tour d’argent", path=path))
    result = api.get(body["result_url"], headers=bearer(secret))
    assert result.status_code == 200, result.text
    assert "Alice entered the" in entries(result.content)[path].decode()
    report = api.get(body["status_url"], headers=bearer(secret)).json()["report"]
    assert report["outcome"] == "completed_with_residuals"
    repair = report["delivery"]["repairs"][0]
    assert repair["attempt"] == 1 and repair["files"] == [path] and repair["passages_restored_to_source"] >= 1
    assert {item["reason"] for item in report["residuals"]} == {"epubcheck_repair"}
    assert report["delivery"]["validation"]["valid"] is True


@respx.mock
async def test_an_epub_that_cannot_be_repaired_fails_with_the_reason(
    owner, api, provider_id, book_bytes, monkeypatch
):
    respx.post(LLM).mock(side_effect=mock_completion)
    monkeypatch.setattr(settings(), "delivery_repair_attempts", 2)
    calls = []

    def always_invalid(content):
        calls.append(1)
        # The original is valid: every error comes from the rebuilt book, even in its source text.
        valid = len(calls) == 2
        return {"available": True, "valid": valid, "report": {"messages": [] if valid else [
            {"ID": "PKG-999", "severity": "FATAL", "message": "Broken", "locations": []}]}}  # fmt: skip

    monkeypatch.setattr("app.engines.delivery.epub.epubcheck", always_invalid)
    secret, body = await translated_epub_request(api, owner, provider_id, book_bytes)
    status = api.get(body["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "failed"
    assert status["error"] == "EPUBCheck refuse l’EPUB traduit, même après réparation automatique."
    assert status["report"]["delivery"]["errors"] == ["PKG-999 — Broken"]
    # The book, the original once, then the book all in its source: nothing is left to repair.
    assert len(calls) == 3
    english = api.get(body["status_url"], headers=bearer(secret, **{"Accept-Language": "en"})).json()
    assert english["error"] == "EPUBCheck rejects the translated EPUB, even after automatic repair."
    refused = api.get(body["result_url"], headers=bearer(secret))
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "request_failed"


@respx.mock
async def test_errors_the_original_already_had_are_inherited_not_blocking(owner, api, provider_id, book_bytes,
                                                                         monkeypatch):  # fmt: skip
    respx.post(LLM).mock(side_effect=mock_completion)
    monkeypatch.setattr("app.engines.delivery.epub.epubcheck", fake_check(None, original_valid=False))
    secret, body = await translated_epub_request(api, owner, provider_id, book_bytes)
    report = api.get(body["status_url"], headers=bearer(secret)).json()["report"]
    assert report["outcome"] == "completed" and report["delivery"]["repairs"] == []
    assert report["delivery"]["inherited_errors"] == ["RSC-005 — Bad markup"]


def test_a_stalled_or_overdue_request_fails_instead_of_running_forever(owner, api, provider_id, monkeypatch):
    secret = new_token(owner)
    created = api.post(REQUESTS, headers=bearer(secret), json=payload(provider_id)).json()
    assert api.post(created["status_url"] + "/pause", headers=bearer(secret)).json()["status"] == "paused"
    dispatch()
    with SessionLocal() as db:
        request = db.get(TranslationRequest, created["request_id"])
        assert request.status == "running" and request.options["stalled_since"]
        request.options = {**request.options, "stalled_since": time.time() - 7 * 3600}
        db.commit()
    dispatch()
    status = api.get(created["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "failed" and "« paused » plus de 360 min" in status["error"]
    assert status["report"]["outcome"] == "failed"
    with SessionLocal() as db:
        assert db.get(Job, created["job_id"]).status == "cancelled"

    later = api.post(REQUESTS, headers=bearer(secret), json=payload(provider_id, external_id="later")).json()
    assert later["status"] == "pending"
    with SessionLocal() as db:
        db.get(TranslationRequest, later["request_id"]).created_at = time.time() - 200 * 3600
        db.commit()
    status = api.get(later["status_url"], headers=bearer(secret)).json()
    assert status["status"] == "failed" and "délai maximal dépassé" in status["error"]


def test_long_polling_is_bounded_and_cancelling_ends_the_request(owner, api, provider_id, monkeypatch):
    monkeypatch.setattr(settings(), "api_result_max_wait_seconds", 1)
    secret = new_token(owner)
    created = api.post(REQUESTS, headers=bearer(secret), json=payload(provider_id)).json()
    waited = api.get(created["result_url"] + "?wait=600", headers=bearer(secret))
    assert waited.status_code == 409 and waited.json()["detail"]["code"] == "result_not_ready"
    assert waited.headers["retry-after"] == "5"
    assert api.get(created["status_url"] + "?wait=600", headers=bearer(secret)).json()["status"] == "pending"
    assert api.get(created["status_url"] + "?wait=100000", headers=bearer(secret)).status_code == 422
    cancelled = api.post(created["status_url"] + "/cancel", headers=bearer(secret)).json()
    assert cancelled["status"] == "cancelled" and cancelled["report"]["outcome"] == "cancelled"
    refused = api.get(created["result_url"], headers=bearer(secret))
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "request_cancelled"


PUBLIC = "93.184.216.34"


@pytest.fixture
def webhook_settings(monkeypatch):
    monkeypatch.setattr(settings(), "api_webhook_hosts", "hooks.example.test,*.partner.test")
    monkeypatch.setattr(webhooks, "resolve", lambda host, port: [PUBLIC])


def test_callbacks_are_refused_unless_allowed_public_and_signed(owner, api, provider_id, monkeypatch):
    secret = new_token(owner)

    def refused(url: str) -> str:
        response = api.post(REQUESTS, headers=bearer(secret), json=payload(provider_id, callback_url=url))
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "callback_refused"
        return response.json()["detail"]["message"]

    assert "API_WEBHOOK_HOSTS" in refused("https://hooks.example.test/x")
    monkeypatch.setattr(settings(), "api_webhook_hosts", "hooks.example.test,*.partner.test")
    monkeypatch.setattr(webhooks, "resolve", lambda host, port: [PUBLIC])
    assert "liste des webhooks autorisés" in refused("https://evil.test/x")
    assert "sans identifiants" in refused("https://user:pw@hooks.example.test/x")
    assert "secret" in refused("https://a.partner.test/x")  # no token secret, no global secret
    monkeypatch.setattr(settings(), "api_webhook_secret", "s" * 40)
    for address in ("10.0.0.5", "127.0.0.1", "169.254.169.254", "::1", "::ffff:192.168.1.1"):
        monkeypatch.setattr(webhooks, "resolve", lambda host, port, a=address: [a])
        assert "adresse privée" in refused("https://hooks.example.test/x"), address
    monkeypatch.setattr(settings(), "api_webhook_private_networks", "10.0.0.0/8")
    monkeypatch.setattr(webhooks, "resolve", lambda host, port: ["10.0.0.5"])
    accepted = api.post(REQUESTS, headers=bearer(secret),
                        json=payload(provider_id, callback_url="https://hooks.example.test/x"))  # fmt: skip
    assert accepted.status_code == 202, accepted.text


@respx.mock
async def test_the_worker_delivers_a_signed_webhook_with_bounded_retries(owner, api, provider_id, webhook_settings,
                                                                         monkeypatch):  # fmt: skip
    respx.post(LLM).mock(side_effect=mock_completion)
    token = owner.post("/api/tokens", json={"name": "Hooks", "scopes": ["content:write", "pipeline:start",
                                                                        "jobs:read", "results:read", "jobs:control"],
                                            "webhook_secret": True}).json()  # fmt: skip
    signing = token["webhook_secret"]
    assert len(signing) > 30 and owner.get("/api/tokens").json()[0]["webhook_secret"] is True
    assert signing not in json.dumps(owner.get("/api/tokens").json())
    received = []

    def hook(request):
        received.append(request)
        return httpx.Response(500 if len(received) == 1 else 204)

    respx.post(host=PUBLIC, path="/libris").mock(side_effect=hook)
    body = payload(provider_id, callback_url="https://hooks.example.test/libris")
    body["pipeline"]["final_review"] = False
    created = api.post(REQUESTS, headers=bearer(token["token"]), json=body).json()
    assert (await run_pending_job()).status == "completed"
    status = api.get(created["status_url"], headers=bearer(token["token"])).json()
    assert status["status"] == "completed" and status["webhook"] == {
        "state": "pending",
        "attempts": 0,
        "error": "",
    }

    now = time.time()
    assert webhooks.pump(now) == 1  # 500: retried later
    assert webhooks.pump(now + 1) == 0  # not due yet: the backoff holds
    assert webhooks.pump(now + webhooks.BACKOFF_BASE + 1) == 1
    status = api.get(created["status_url"], headers=bearer(token["token"])).json()
    assert status["webhook"] == {"state": "delivered", "attempts": 2, "error": ""}
    last = received[-1]
    assert last.headers["host"] == "hooks.example.test" and last.url.host == PUBLIC
    timestamp = last.headers["x-libris-timestamp"]
    expected = hmac.new(
        signing.encode(), timestamp.encode() + b"." + last.content, hashlib.sha256
    ).hexdigest()
    assert last.headers["x-libris-signature"] == f"sha256={expected}"
    event = json.loads(last.content)
    assert event["status"] == "completed" and event["request_id"] == created["request_id"]
    assert event["report"]["outcome"] == "completed" and event["artifact"]["format"] == "json"

    # A receiver that never answers 2xx: given up after API_WEBHOOK_MAX_ATTEMPTS, the status still final.
    monkeypatch.setattr(settings(), "api_webhook_max_attempts", 2)
    monkeypatch.setattr(settings(), "api_webhook_secret", "g" * 40)
    respx.post(host=PUBLIC, path="/down").mock(return_value=httpx.Response(503))
    plain = new_token(owner)
    failing = api.post(REQUESTS, headers=bearer(plain), json=payload(
        provider_id, external_id="down", callback_url="https://x.partner.test/down", pipeline={"start": False}))  # fmt: skip
    assert failing.status_code == 202 and failing.json()["status"] == "imported"
    for step in range(3):
        webhooks.pump(time.time() + step * webhooks.BACKOFF_MAX)
    with SessionLocal() as db:
        row = db.get(TranslationRequest, failing.json()["request_id"])
        assert (row.webhook_state, row.webhook_attempts, row.webhook_error) == ("failed", 2, "HTTP 503")
