"""Webhooks of automation requests: one signed POST when a request ends, sent by the worker.

Only hosts an administrator listed (API_WEBHOOK_HOSTS) can be called. The name is resolved when the
request is accepted and again before every call, and the call goes to the address checked (no second
resolution a DNS rebinding could change); private, loopback, link-local and other non-public addresses
are refused unless they are inside API_WEBHOOK_PRIVATE_NETWORKS. Redirects are not followed.

The body is signed with HMAC-SHA256 — the token's own webhook secret, else API_WEBHOOK_SECRET — over
`<timestamp>.<body>`, sent as `X-Libris-Signature: sha256=<hex>` with `X-Libris-Timestamp`. Failed calls
are retried with an exponential backoff, API_WEBHOOK_MAX_ATTEMPTS times at most; the client can always
poll the status instead.
"""

import fnmatch
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import time
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import ApiToken, Job, Project, TranslationRequest
from app.security import SecretUnreadable, decrypt

logger = logging.getLogger("epub.webhooks")
BACKOFF_BASE = 30
BACKOFF_MAX = 3600
BATCH = 20


class WebhookRefused(ValueError):
    pass


def allowed_hosts() -> list[str]:
    return [
        item.strip().casefold().rstrip(".")
        for item in settings().api_webhook_hosts.split(",")
        if item.strip()
    ]


def private_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for item in settings().api_webhook_private_networks.split(","):
        if item.strip():
            networks.append(ipaddress.ip_network(item.strip(), strict=False))
    return networks


def host_allowed(host: str) -> bool:
    host = host.casefold().rstrip(".")
    return any(host == pattern or (pattern.startswith("*.") and fnmatch.fnmatch(host, pattern))
               for pattern in allowed_hosts())  # fmt: skip


def resolve(host: str, port: int) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError):
        raise WebhookRefused("L’adresse de rappel ne peut pas être résolue.") from None


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in network for network in private_networks()):
        return True
    return ip.is_global and not ip.is_multicast


def checked_url(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, address to call) of an allowed callback, or WebhookRefused."""
    if not allowed_hosts():
        raise WebhookRefused("Les webhooks ne sont pas activés sur ce serveur (API_WEBHOOK_HOSTS).")
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise WebhookRefused("Adresse de rappel invalide.") from None
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise WebhookRefused("Adresse de rappel invalide : une URL HTTP(S) sans identifiants est requise.")
    if len(url) > 2000:
        raise WebhookRefused("Adresse de rappel trop longue.")
    host = parts.hostname
    if not host_allowed(host):
        raise WebhookRefused(f"L’hôte « {host} » n’est pas dans la liste des webhooks autorisés.")
    addresses = resolve(host, port)
    refused = [address for address in addresses if not public_address(address)]
    if refused or not addresses:
        raise WebhookRefused(f"L’hôte « {host} » désigne une adresse privée ou réservée : webhook refusé.")
    return parts.scheme, host, port, addresses[0]


def signing_secret(db, request: TranslationRequest) -> str:
    token = db.get(ApiToken, request.token_id or "")
    if token is not None and token.webhook_secret:
        try:
            return decrypt(token.webhook_secret)
        except SecretUnreadable:
            logger.warning("request=%s webhook=token_secret_unreadable", request.id)
    return settings().api_webhook_secret


def require_signing(token: ApiToken) -> None:
    if not token.webhook_secret and not settings().api_webhook_secret:
        raise WebhookRefused(
            "Aucun secret de signature : créez le jeton avec un secret de webhook, ou définissez API_WEBHOOK_SECRET."
        )


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def event(db, request: TranslationRequest) -> dict:
    base = f"/api/v1/translation-requests/{request.id}"
    job = db.get(Job, request.job_id or "") if request.job_id else None
    project = db.get(Project, request.project_id or "") if request.project_id else None
    return {
        "event": "translation_request.finished",
        "request_id": request.id,
        "external_id": request.external_id,
        "status": request.status,
        "error": request.error or None,
        "project_id": project.id if project else None,
        "job_id": job.id if job else None,
        "status_url": base,
        "result_url": f"{base}/result",
        "artifact": {key: (request.artifact or {}).get(key) for key in ("format", "size", "sha256")}
        if request.artifact
        else None,
        "report": request.report,
        "finished_at": request.finished_at,
    }


def backoff(attempts: int) -> float:
    return min(BACKOFF_MAX, BACKOFF_BASE * 2 ** max(0, attempts - 1))


def send(url: str, body: bytes, headers: dict) -> int:
    """POST to the address checked just now, keeping the name for the Host header and TLS."""
    scheme, host, port, address = checked_url(url)
    parts = urlsplit(url)
    literal = f"[{address}]" if ":" in address else address
    target = parts._replace(netloc=f"{literal}:{port}").geturl()
    with httpx.Client(
        timeout=settings().api_webhook_timeout_seconds, follow_redirects=False, trust_env=False
    ) as client:
        response = client.post(
            target,
            content=body,
            headers={**headers, "Host": parts.netloc.rsplit("@", 1)[-1]},
            extensions={"sni_hostname": host} if scheme == "https" else {},
        )
    return response.status_code


def deliver_one(request_id: str, now: float | None = None) -> str | None:
    """One attempt for one request; returns its new webhook state (None when not due)."""
    now = now or time.time()
    with SessionLocal() as db:
        query = select(TranslationRequest).where(
            TranslationRequest.id == request_id,
            TranslationRequest.webhook_state == "pending",
            TranslationRequest.webhook_next_attempt <= now,
        )
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        request = db.scalar(query)
        if request is None or not request.callback_url:
            return None
        body = json.dumps(event(db, request), ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(now))
        secret = signing_secret(db, request)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Libris-Webhook/1",
            "X-Libris-Event": "translation_request.finished",
            "X-Libris-Delivery": f"{request.id}:{request.webhook_attempts + 1}",
            "X-Libris-Timestamp": timestamp,
        }
        error = ""
        if not secret:
            error = "Aucun secret de signature disponible."
        else:
            headers["X-Libris-Signature"] = signature(secret, timestamp, body)
            try:
                status = send(request.callback_url, body, headers)
                if not 200 <= status < 300:
                    error = f"HTTP {status}"
            except WebhookRefused as exc:
                error = str(exc)
            except httpx.HTTPError as exc:
                error = type(exc).__name__
        request.webhook_attempts += 1
        if not error:
            request.webhook_state, request.webhook_error = "delivered", ""
        elif request.webhook_attempts >= settings().api_webhook_max_attempts:
            request.webhook_state, request.webhook_error = "failed", error[:500]
        else:
            request.webhook_error = error[:500]
            request.webhook_next_attempt = now + backoff(request.webhook_attempts)
        logger.info(
            "request=%s webhook=%s attempt=%s", request.id, request.webhook_state, request.webhook_attempts
        )
        state = request.webhook_state
        db.commit()
        return state


def pump(now: float | None = None) -> int:
    """One pass over the webhooks due; returns how many calls were attempted."""
    now = now or time.time()
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(TranslationRequest.id)
                .where(
                    TranslationRequest.webhook_state == "pending",
                    TranslationRequest.webhook_next_attempt <= now,
                )
                .order_by(TranslationRequest.webhook_next_attempt)
                .limit(BATCH)
            )
        )
    return sum(1 for request_id in due if deliver_one(request_id, now) is not None)
