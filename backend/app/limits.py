"""Bound request bodies before anything is buffered: uploads are spooled to disk ahead of authentication."""

import json
from http.cookies import SimpleCookie

from app.config import settings
from app.i18n import localize, preferred_language

ANONYMOUS_LIMIT = 1024**2  # login and other unauthenticated calls are small JSON documents


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope["headers"]}
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
        except Exception:  # noqa: BLE001 - a malformed cookie header is simply "no session"
            cookies = SimpleCookie()
        # The automation API authenticates with a Bearer token, never with the session cookie.
        automation = scope["path"].startswith("/api/v1/")
        if automation:
            anonymous = not headers.get("authorization", "").casefold().startswith("bearer ")
            megabytes = settings().api_payload_mb
        else:
            anonymous = "epub_session" not in cookies
            megabytes = settings().max_upload_mb
        # Multipart framing adds a little to the file itself.
        limit = ANONYMOUS_LIMIT if anonymous else megabytes * 1024**2 + ANONYMOUS_LIMIT
        status, detail = (
            (401, "Authentification requise.")
            if anonymous
            else (413, f"Requête trop volumineuse : {megabytes} Mo au maximum.")
        )
        detail = localize(detail, preferred_language(headers.get("accept-language")))
        if automation:
            detail = {"code": "unauthorized" if anonymous else "payload_too_large", "message": detail}

        async def reject():
            body = json.dumps({"detail": detail}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"connection", b"close"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            return await reject()
        received = 0
        rejected = False

        async def bounded_receive():
            nonlocal received, rejected
            message = await receive()
            if message["type"] == "http.request" and not rejected:
                received += len(message.get("body", b""))
                if received > limit:  # chunked transfer, or a Content-Length that lied
                    # Answer ourselves, then tell the application its client is gone: whatever it
                    # was buffering stops here, and its own late answer is discarded below.
                    rejected = True
                    await reject()
                    return {"type": "http.disconnect"}
            return {"type": "http.disconnect"} if rejected else message

        async def guarded_send(message):
            if not rejected:
                await send(message)

        try:
            await self.app(scope, bounded_receive, guarded_send)
        except Exception:
            if not rejected:
                raise
