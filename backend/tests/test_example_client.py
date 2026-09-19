"""examples/libris_client.py, the standard-library client, against the real automation API: an EPUB and
TXT chapters in, the translation out, errors, rate limits and signed webhooks."""

import importlib.util
import io
import json
import sys
import threading
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import respx
import test_api_v1
from test_api_delivery import LLM, entries
from test_api_v1 import new_token, run_pending_job
from test_pipeline import mock_completion

from app.engines.delivery.webhooks import signature

provider_id, owner, api = test_api_v1.provider_id, test_api_v1.owner, test_api_v1.api
SOURCE = Path(__file__).resolve().parents[2] / "examples/libris_client.py"
spec = importlib.util.spec_from_file_location("libris_client", SOURCE)
client_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client_module  # dataclasses look their module up
spec.loader.exec_module(client_module)


def through(test_client):
    """A transport that sends the client's HTTP requests to the application in-process."""

    def send(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        parts = urlsplit(url)
        answer = test_client.request(
            method, parts.path + (f"?{parts.query}" if parts.query else ""), headers=headers, content=body
        )
        return client_module.Response(
            answer.status_code, {k.lower(): v for k, v in answer.headers.items()}, answer.content
        )

    return send


@pytest.fixture
def client(owner, api):
    return client_module.LibrisClient("https://libris.example.test", new_token(owner), transport=through(api))


def test_the_client_uses_the_standard_library_only():
    imported = {
        line.split()[1].split(".")[0]
        for line in SOURCE.read_text().splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {
        "__future__", "argparse", "hashlib", "hmac", "json", "os", "sys", "time", "urllib", "uuid", "dataclasses",
        "http", "pathlib", "typing",
    }  # fmt: skip


@respx.mock
async def test_an_epub_goes_in_and_the_translated_epub_comes_out(client, provider_id, book_bytes, tmp_path):
    respx.post(LLM).mock(side_effect=mock_completion)
    options = {
        "series": "Silver Saga", "volume": 1, "source_language": "en", "target_language": "fr",
        "provider_id": provider_id, "quality": "fast", "final_review": False,
    }  # fmt: skip
    created = client.submit_epub(book_bytes, "The Silver Tower.epub", idempotency_key="tower-1", **options)
    assert created["input"] == "epub" and created["status"] == "pending" and not created["replayed"]
    again = client.submit_epub(book_bytes, "The Silver Tower.epub", idempotency_key="tower-1", **options)
    assert again["replayed"] and again["request_id"] == created["request_id"]

    assert (await run_pending_job()).status == "completed"
    seen = []
    final = client.wait(created["request_id"], poll=1, progress=seen.append)
    assert final["status"] == "completed" and seen[-1] is final and final["report"]["outcome"] == "completed"
    result = client.download(created["request_id"], tmp_path / "tower.fr.epub")
    assert result.complete and result.status == "completed" and result.media_type == "application/epub+zip"
    assert result.filename.endswith(".epub")
    book = entries((tmp_path / "tower.fr.epub").read_bytes())
    assert any("Tour d’argent" in content.decode(errors="ignore") for content in book.values())
    document = client.result(created["request_id"], format="json")
    assert json.loads(document.content)["status"] == "completed"
    assert client.providers()[0]["id"] == provider_id and client.series()[0]["name"] == "Silver Saga"


def test_txt_chapters_go_in_with_their_names(client, provider_id):
    chapters = [
        ("Chapter 2.txt", "Bob waited at the gate.\n"),
        ("Chapitre 1 — « Élodie ».txt", "Élodie opened the gate.\n".encode()),
    ]
    created = client.submit_chapters(
        chapters, series="Web Saga", volume=1, source_language="en", target_language="fr",
        provider_id=provider_id, start=False, output_format="txt-zip",
    )  # fmt: skip
    assert created["input"] == "txt" and created["status"] == "imported"
    status = client.wait(created["request_id"], poll=1)
    items = status["chapters"]["items"]
    assert [item["number"] for item in items] == [1, 2] and items[0]["title"] == "Chapitre 1 — « Élodie »"
    partial = client.result(created["request_id"], partial=True)
    assert not partial.complete and partial.media_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(partial.content)) as archive:
        assert "manifest.json" in archive.namelist()


def test_errors_carry_their_code_and_rate_limits_are_waited_out(client):
    with pytest.raises(client_module.LibrisError) as caught:
        client.status("unknown-request")
    assert caught.value.status == 404 and caught.value.code == "request_not_found"
    assert caught.value.message and client.token not in str(caught.value)

    answers = [
        client_module.Response(
            429, {"retry-after": "2"}, b'{"detail": {"code": "rate_limited", "message": "x"}}'
        ),
        client_module.Response(200, {}, b"[]"),
    ]
    waited = []
    limited = client_module.LibrisClient(
        "https://libris.example.test", "lbr_test", transport=lambda *args: answers.pop(0), sleep=waited.append
    )
    assert limited.series() == [] and waited == [2.0]
    broken = client_module.LibrisClient(
        "https://libris.example.test",
        "lbr_test",
        transport=lambda *a: client_module.Response(502, {}, b"<html>"),
    )
    with pytest.raises(client_module.LibrisError) as caught:
        broken.series()
    assert caught.value.status == 502 and caught.value.code == "error"
    with pytest.raises(ValueError):
        client_module.LibrisClient("libris.example.test", "lbr_test")


def test_webhook_signatures_are_checked_like_libris_signs_them():
    secret, body = "s" * 40, b'{"event":"translation_request.finished","status":"completed"}'
    now = time.time()
    timestamp = str(int(now))
    signed = signature(secret, timestamp, body)
    verify = client_module.verify_signature
    assert verify(secret, body, timestamp, signed, now=now)
    assert not verify(secret, body + b" ", timestamp, signed, now=now)
    assert not verify("t" * 40, body, timestamp, signed, now=now)
    assert not verify(secret, body, timestamp, signed, now=now + 301)  # replayed later: refused
    assert not verify(secret, body, "not-a-time", signed, now=now)


def test_the_webhook_receiver_accepts_only_signed_events():
    secret = "w" * 40
    events = []
    server = client_module.webhook_server("127.0.0.1", 0, secret, events.append, path="/libris")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/libris"
        body = json.dumps({"event": "translation_request.finished", "request_id": "r1"}).encode()
        timestamp = str(int(time.time()))
        headers = {"Content-Type": "application/json", "X-Libris-Timestamp": timestamp}
        send = client_module.urllib_transport
        good = send(
            "POST", url, {**headers, "X-Libris-Signature": signature(secret, timestamp, body)}, body, 5
        )
        bad = send("POST", url, {**headers, "X-Libris-Signature": "sha256=" + "0" * 64}, body, 5)
        assert (good.status, bad.status) == (204, 401)
        assert events == [{"event": "translation_request.finished", "request_id": "r1"}]
    finally:
        server.shutdown()
        server.server_close()
