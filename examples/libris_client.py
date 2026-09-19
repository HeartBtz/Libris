#!/usr/bin/env python3
"""Example client of the Libris automation API (`/api/v1`): Python 3.10+, standard library only.

Send an EPUB, TXT chapters or a JSON document, wait for the translation, download the result, and
receive signed webhooks. Use it as a command, or import `LibrisClient` in your own code.

    export LIBRIS_URL=https://libris.example.org
    export LIBRIS_TOKEN=lbr_xxxxxxxx_...          # My account › API tokens (shown once)

    # An EPUB in, the translated EPUB out
    python3 libris_client.py epub "The Silver Tower.epub" --target-language fr --out tower.fr.epub

    # TXT chapters in (one file per chapter), a ZIP of translated chapters out
    python3 libris_client.py chapters "Chapter 1.txt" "Chapter 2.txt" --series "Web Saga" --volume 1 \\
        --source-language en --target-language fr --format txt-zip --out volume-1.zip

    # A JSON document (see docs/api.md), the JSON result out
    python3 libris_client.py json request.json --out result.json

    # Receive webhooks and check their signature (the token's webhook secret, or API_WEBHOOK_SECRET)
    LIBRIS_WEBHOOK_SECRET=... python3 libris_client.py webhooks --port 8080

Options such as --provider-id, --quality or --series are the fields documented in docs/api.md and in
the OpenAPI description (docs/openapi/libris-v1.json). The token is sent to LIBRIS_URL only, and never
printed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

ENDED = {"completed", "completed_with_residuals", "failed", "cancelled", "imported"}
SUCCESS = {"completed", "completed_with_residuals"}
USER_AGENT = "libris-example-client/1"


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self):
        return json.loads(self.body)


# A transport sends one HTTP request: (method, url, headers, body, timeout) -> Response.
Transport = Callable[[str, str, dict, bytes | None, float], Response]


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> Response:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return Response(answer.status, {k.lower(): v for k, v in answer.headers.items()}, answer.read())
    except urllib.error.HTTPError as error:
        with error:
            return Response(error.code, {k.lower(): v for k, v in error.headers.items()}, error.read())


class LibrisError(Exception):
    """An error answer of the API: `status`, the stable `code`, the `message` and the whole `detail`."""

    def __init__(self, status: int, detail: dict):
        self.status = status
        self.detail = detail
        self.code = str(detail.get("code", "error"))
        self.message = str(detail.get("message", ""))
        super().__init__(f"HTTP {status} {self.code}: {self.message}")


@dataclass
class Result:
    content: bytes
    media_type: str
    complete: bool
    status: str
    filename: str | None = None


def form_value(value) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def quoted(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


def multipart(fields: dict, files: list[tuple[str, str, bytes, str]]) -> tuple[bytes, str]:
    """multipart/form-data body: fields, then (field, file name, content, media type) files."""
    boundary = "libris-" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        if value is None:
            continue
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted(name)}"\r\n\r\n'.encode()
            + form_value(value).encode()
            + b"\r\n"
        )
    for name, filename, content, media_type in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted(name)}"; '
            f'filename="{quoted(filename)}"\r\nContent-Type: {media_type}\r\n\r\n'.encode()
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@dataclass
class LibrisClient:
    base_url: str
    token: str
    timeout: float = 120.0
    transport: Transport = urllib_transport
    retries: int = 5  # answers 429 (rate limit) are retried after their Retry-After
    language: str = "en"  # language of the error messages
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        parts = urllib.parse.urlsplit(self.base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("base_url must be an http(s) URL, for example https://libris.example.org")
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            print("warning: the token is sent over plain HTTP; use https://", file=sys.stderr)

    # --- HTTP ---

    def call(self, method: str, path: str, *, body: bytes | None = None, content_type: str | None = None,
             query: dict | None = None, headers: dict | None = None, timeout: float | None = None) -> Response:  # fmt: skip
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode({k: form_value(v) for k, v in query.items() if v is not None})
        sent = {
            "Authorization": f"Bearer {self.token}",
            "Accept-Language": self.language,
            "User-Agent": USER_AGENT,
            **(headers or {}),
        }
        if content_type:
            sent["Content-Type"] = content_type
        for attempt in range(self.retries + 1):
            answer = self.transport(method, url, sent, body, timeout or self.timeout)
            if answer.status == 429 and attempt < self.retries:
                self.sleep(float(answer.headers.get("retry-after") or 1))
                continue
            break
        if answer.status >= 400:
            try:
                detail = answer.json().get("detail")
            except (ValueError, AttributeError):  # not JSON, or not an object: a proxy's error page
                detail = None
            raise LibrisError(
                answer.status, detail if isinstance(detail, dict) else {"message": str(detail or "")}
            )
        return answer

    # --- Sending content ---

    def submit_json(self, document: dict, idempotency_key: str | None = None) -> dict:
        """A JSON translation request (docs/api.md, "Send a JSON document")."""
        body = json.dumps(document, ensure_ascii=False).encode()
        return self._created(self.call("POST", "/api/v1/translation-requests", body=body,
                                       content_type="application/json", headers=self._key(idempotency_key)))  # fmt: skip

    def submit_epub(self, epub: bytes | str | Path, filename: str | None = None,
                    idempotency_key: str | None = None, **options) -> dict:  # fmt: skip
        """An EPUB and its options (target_language, provider_id, series, volume, quality…)."""
        content, name = self._read(epub, filename or "book.epub")
        body, kind = multipart(options, [("file", name, content, "application/epub+zip")])
        return self._created(self.call("POST", "/api/v1/translation-requests", body=body, content_type=kind,
                                       headers=self._key(idempotency_key)))  # fmt: skip

    def submit_chapters(self, chapters: list, idempotency_key: str | None = None, **options) -> dict:
        """TXT chapters, one per file: paths or (file name, text or bytes). Needs series (or series_id),
        volume, source_language and target_language; chapter numbers come from the file names."""
        files = []
        for item in chapters:
            if isinstance(item, tuple):
                name, text = item
                content = text.encode() if isinstance(text, str) else text
            else:
                content, name = self._read(item, "")
            files.append(("files", name, content, "text/plain; charset=utf-8"))
        body, kind = multipart(options, files)
        return self._created(self.call("POST", "/api/v1/translation-requests", body=body, content_type=kind,
                                       headers=self._key(idempotency_key)))  # fmt: skip

    # --- Following a request ---

    def status(self, request_id: str, wait: int = 0) -> dict:
        """The status document; with `wait`, answers when the request ends or after `wait` seconds."""
        timeout = self.timeout + wait
        return self.call("GET", f"/api/v1/translation-requests/{quote(request_id)}",
                         query={"wait": wait or None}, timeout=timeout).json()  # fmt: skip

    def wait(self, request_id: str, deadline: float | None = None, poll: int = 60,
             progress: Callable[[dict], None] | None = None) -> dict:  # fmt: skip
        """Long-polls until the request ends (or `deadline` seconds pass); returns the last status."""
        start = time.monotonic()
        while True:
            document = self.status(request_id, wait=poll)
            if progress:
                progress(document)
            if document["status"] in ENDED:
                return document
            if deadline is not None and time.monotonic() - start >= deadline:
                raise TimeoutError(f"request {request_id} still {document['status']} after {deadline} s")

    def control(self, request_id: str, action: str) -> dict:
        """`pause`, `resume` or `cancel`."""
        return self.call("POST", f"/api/v1/translation-requests/{quote(request_id)}/{quote(action)}").json()

    def result(self, request_id: str, format: str | None = None, partial: bool = False) -> Result:  # noqa: A002
        """The result: `format` is epub, json, txt or txt-zip (default: the request's own format)."""
        answer = self.call("GET", f"/api/v1/translation-requests/{quote(request_id)}/result",
                           query={"format": format, "partial": partial or None})  # fmt: skip
        disposition = answer.headers.get("content-disposition", "")
        filename = None
        if "filename*=UTF-8''" in disposition:
            filename = urllib.parse.unquote(disposition.split("filename*=UTF-8''", 1)[1].split(";")[0])
        return Result(
            content=answer.body,
            media_type=answer.headers.get("content-type", ""),
            complete=answer.headers.get("x-libris-complete") == "true",
            status=answer.headers.get("x-libris-status", ""),
            filename=Path(filename).name if filename else None,
        )

    def download(self, request_id: str, destination: str | Path, format: str | None = None,  # noqa: A002
                 partial: bool = False) -> Result:  # fmt: skip
        result = self.result(request_id, format, partial)
        Path(destination).write_bytes(result.content)
        return result

    # --- Lookups ---

    def providers(self) -> list[dict]:
        return self.call("GET", "/api/v1/providers").json()

    def series(self) -> list[dict]:
        return self.call("GET", "/api/v1/series").json()

    # --- Helpers ---

    @staticmethod
    def _key(idempotency_key: str | None) -> dict:
        return {"Idempotency-Key": idempotency_key} if idempotency_key else {}

    @staticmethod
    def _read(source, default_name: str) -> tuple[bytes, str]:
        if isinstance(source, bytes):
            return source, default_name
        path = Path(source)
        return path.read_bytes(), path.name

    @staticmethod
    def _created(answer: Response) -> dict:
        document = answer.json()
        document["replayed"] = answer.headers.get("idempotent-replayed") == "true"
        return document


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


# --- Webhooks --------------------------------------------------------------------------------------------


def verify_signature(secret: str, body: bytes, timestamp: str, signature: str, tolerance: int = 300,
                     now: float | None = None) -> bool:  # fmt: skip
    """True when `X-Libris-Signature` is the HMAC-SHA256 of `<timestamp>.<raw body>` and the
    `X-Libris-Timestamp` is at most `tolerance` seconds away (old deliveries replayed are refused)."""
    try:
        age = abs((time.time() if now is None else now) - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > tolerance:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature or "", "sha256=" + expected)


def webhook_server(host: str, port: int, secret: str, on_event: Callable[[dict], None],
                   path: str = "/", max_body: int = 10 * 1024**2) -> ThreadingHTTPServer:  # fmt: skip
    """An HTTP server that accepts Libris webhooks on `path`: 204 when the signature is valid (the event
    is passed to `on_event`), 401 otherwise, so Libris retries. Put it behind HTTPS in production."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server's naming
            length = int(self.headers.get("Content-Length") or 0)
            if urllib.parse.urlsplit(self.path).path != path or not 0 < length <= max_body:
                self.send_error(404 if length else 400)
                return
            body = self.rfile.read(length)
            valid = verify_signature(
                secret,
                body,
                self.headers.get("X-Libris-Timestamp", ""),
                self.headers.get("X-Libris-Signature", ""),
            )
            if not valid:
                self.send_error(401, "invalid signature")
                return
            on_event(json.loads(body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):  # quiet: events are reported by on_event
            pass

    return ThreadingHTTPServer((host, port), Handler)


# --- Command line ----------------------------------------------------------------------------------------

OPTIONS = ("series", "series_id", "volume", "external_id", "volume_external_id", "title", "author",
           "source_language", "target_language", "provider_id", "quality", "context_backend", "callback_url")  # fmt: skip


def client_from_env() -> LibrisClient:
    url, token = os.environ.get("LIBRIS_URL", ""), os.environ.get("LIBRIS_TOKEN", "")
    if not url or not token:
        sys.exit("Set LIBRIS_URL and LIBRIS_TOKEN.")
    return LibrisClient(url, token)


def report(document: dict) -> None:
    progress = document.get("progress") or {}
    print(f"{document['status']}: {progress.get('translated', 0)}/{progress.get('segments', 0)} passages",
          file=sys.stderr)  # fmt: skip


def finish(
    client: LibrisClient, created: dict, out: str | None, fmt: str | None, deadline: float | None
) -> int:
    request_id = created["request_id"]
    print(
        f"request {request_id}{' (already sent before)' if created.get('replayed') else ''}", file=sys.stderr
    )
    document = client.wait(request_id, deadline=deadline, progress=report)
    final = document.get("report") or {}
    print(json.dumps({"status": document["status"], "error": document.get("error") or None,
                      "residual_total": final.get("residual_total"), "usage": final.get("usage")},
                     ensure_ascii=False), file=sys.stderr)  # fmt: skip
    if document["status"] == "imported":
        return 0  # --no-start: the chapters are in Libris, nothing to download
    if document["status"] not in SUCCESS:
        return 1
    result = client.result(request_id, fmt)
    target = Path(out or result.filename or f"result-{request_id}")
    target.write_bytes(result.content)
    print(f"saved {target} ({len(result.content)} bytes, complete={result.complete})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Libris automation API example client.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("epub", "translate an EPUB"), ("chapters", "translate TXT chapters"),
                            ("json", "send a JSON document")):  # fmt: skip
        command = commands.add_parser(name, help=help_text)
        command.add_argument("files", nargs="+" if name == "chapters" else 1)
        command.add_argument("--out", help="where to save the result")
        command.add_argument("--format", choices=["epub", "json", "txt", "txt-zip"], help="result format")
        command.add_argument("--idempotency-key")
        command.add_argument("--no-start", action="store_true", help="import only, do not translate")
        command.add_argument("--deadline", type=float, help="give up waiting after this many seconds")
        if name != "json":
            for option in OPTIONS:
                command.add_argument("--" + option.replace("_", "-"), dest=option)
    hooks = commands.add_parser(
        "webhooks", help="receive and verify webhooks (secret in LIBRIS_WEBHOOK_SECRET)"
    )
    hooks.add_argument("--host", default="127.0.0.1")
    hooks.add_argument("--port", type=int, default=8080)
    hooks.add_argument("--path", default="/")
    args = parser.parse_args(argv)

    if args.command == "webhooks":
        secret = os.environ.get("LIBRIS_WEBHOOK_SECRET", "")
        if not secret:
            sys.exit("Set LIBRIS_WEBHOOK_SECRET.")

        def show(event: dict) -> None:
            print(json.dumps({key: event.get(key) for key in ("event", "request_id", "external_id", "status",
                                                              "result_url")}), flush=True)  # fmt: skip

        server = webhook_server(args.host, args.port, secret, show, args.path)
        print(f"listening on http://{args.host}:{args.port}{args.path}", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    client = client_from_env()
    try:
        if args.command == "json":
            document = json.loads(Path(args.files[0]).read_text(encoding="utf-8"))
            if args.no_start:
                document.setdefault("pipeline", {})["start"] = False
            created = client.submit_json(document, args.idempotency_key)
        else:
            options = {key: getattr(args, key) for key in OPTIONS if getattr(args, key) is not None}
            if args.no_start:
                options["start"] = False
            if args.format:
                options["output_format"] = args.format
            if args.command == "epub":
                created = client.submit_epub(args.files[0], idempotency_key=args.idempotency_key, **options)
            else:
                created = client.submit_chapters(args.files, idempotency_key=args.idempotency_key, **options)
        return finish(client, created, args.out, args.format, args.deadline)
    except LibrisError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
