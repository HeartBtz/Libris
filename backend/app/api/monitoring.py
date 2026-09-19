"""Prometheus exposition of the queue, the model calls and the book pipeline.

Counters are read from the database, where every model call already leaves a row: they are
cumulative since the installation and survive restarts of the API. They only go down when a book
is deleted with its requests, which Prometheus treats as a counter reset. Nothing here names a
book, quotes its text or exposes a provider address.
"""

import hmac
import threading
import time
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import and_, func, or_, select

from app.config import settings
from app.db import SessionLocal
from app.jobs.queue import RUNNING
from app.maintenance.usage import usage
from app.models import Event, Job, Outbox, Provider, RequestLog, Segment

router = APIRouter()

CACHE_SECONDS = 10
# A request that consumed input tokens without producing an applied result.
WASTED_STATUSES = ("error", "refused", "interrupted", "abandoned")
cache: dict[str, tuple[float, str]] = {}
cache_lock = threading.Lock()


def label(value) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Exposition:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def family(self, name: str, kind: str, help_text: str, samples) -> None:
        self.lines += [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
        for labels, value in samples:
            rendered = ",".join(f'{key}="{label(val)}"' for key, val in labels.items())
            self.lines.append(f"{name}{{{rendered}}} {value}" if rendered else f"{name} {value}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def queued_since(db, now: float) -> list[float]:
    # A resumed job keeps its creation date: its last event tells when it went back to the queue.
    last_event = (
        select(Event.created_at)
        .where(Event.project_id == Job.project_id)
        .order_by(Event.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Job.status, Job.created_at, Job.next_attempt, last_event).where(
            or_(Job.status == "pending", and_(Job.status == "waiting", Job.next_attempt <= now))
        )
    ).all()
    return [
        next_attempt if status == "waiting" else max(created_at, event_at or 0)
        for status, created_at, next_attempt, event_at in rows
    ]


def render() -> str:
    now = time.time()
    out = Exposition()
    with SessionLocal() as db:
        jobs = db.execute(
            select(Job.operation, Job.status, func.count()).group_by(Job.operation, Job.status)
        ).all()
        since = queued_since(db, now)
        expired = db.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(RUNNING), Job.lease_until < now)
        )
        # Daily aggregates plus the requests not rolled up yet (app.maintenance.usage).
        calls = [
            (operation, status, cached, count, prompt, completion)
            for operation, status, cached, count, prompt, completion, _duration, _cost in usage(
                db, ("operation", "status", "cached")
            )
            if status != "running"
        ]
        running = db.execute(
            select(Provider.name, func.count(RequestLog.id))
            .outerjoin(
                RequestLog,
                and_(
                    RequestLog.provider_id == Provider.id,
                    RequestLog.status == "running",
                    RequestLog.created_at > now - Provider.timeout - 30,
                ),
            )
            .group_by(Provider.id, Provider.name)
        ).all()
        segments = db.execute(select(Segment.status, func.count()).group_by(Segment.status)).all()
        outbox = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.status != "sent"))

    out.family(
        "libris_jobs", "gauge", "Jobs by operation and state.",
        [({"operation": operation, "status": status}, count) for operation, status, count in sorted(jobs)],
    )
    out.family(
        "libris_jobs_oldest_queued_age_seconds", "gauge",
        "Time since the oldest job ready to run has been waiting for a worker (0 when none).",
        [({}, round(max(0.0, now - min(since)), 3) if since else 0)],
    )
    out.family(
        "libris_jobs_expired_leases", "gauge",
        "Running jobs whose lease expired: their worker stopped renewing it.",
        [({}, expired or 0)],
    )
    requests: dict[tuple, int] = defaultdict(int)
    tokens_in: dict[str, int] = defaultdict(int)
    tokens_out: dict[str, int] = defaultdict(int)
    wasted: dict[str, int] = defaultdict(int)
    hits: dict[str, int] = defaultdict(int)
    for operation, status, cached, count, prompt, completion in calls:
        requests[(operation, status)] += count
        tokens_in[operation] += prompt
        tokens_out[operation] += completion
        if status in WASTED_STATUSES:
            wasted[operation] += prompt
        if cached:
            hits[operation] += count
    operations = sorted(tokens_in)
    out.family(
        "libris_llm_requests_total", "counter", "Finished model requests by operation and outcome.",
        [({"operation": op, "status": status}, count) for (op, status), count in sorted(requests.items())],
    )
    out.family(
        "libris_llm_input_tokens_total", "counter", "Input tokens reported by the providers, by operation.",
        [({"operation": op}, tokens_in[op]) for op in operations],
    )
    out.family(
        "libris_llm_output_tokens_total", "counter", "Output tokens reported by the providers, by operation.",
        [({"operation": op}, tokens_out[op]) for op in operations],
    )
    out.family(
        "libris_llm_wasted_input_tokens_total", "counter",
        "Input tokens of requests that failed, were refused or interrupted, by operation.",
        [({"operation": op}, wasted[op]) for op in operations],
    )
    out.family(
        "libris_llm_cache_hits_total", "counter", "Requests answered from the response cache, by operation.",
        [({"operation": op}, hits[op]) for op in operations],
    )
    total = sum(requests.values())
    out.family(
        "libris_llm_cache_hit_ratio", "gauge", "Share of all finished requests answered from the cache.",
        [({}, round(sum(hits.values()) / total, 6) if total else 0)],
    )
    by_provider: dict[str, int] = defaultdict(int)
    for name, count in running:
        by_provider[name] += count
    out.family(
        "libris_llm_requests_in_flight", "gauge", "Model requests in progress, by provider name.",
        [({"provider": name}, count) for name, count in sorted(by_provider.items())],
    )
    out.family(
        "libris_segments", "gauge", "Passages of all books, by state.",
        [({"status": status}, count) for status, count in sorted(segments)],
    )
    out.family(
        "libris_memory_outbox_pending", "gauge", "External-memory updates not yet delivered.",
        [({}, outbox or 0)],
    )
    return out.text()


def authorized(request: Request, token: str) -> bool:
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    # Constant-time comparison: timing reveals at most the token length, not its content.
    return scheme.lower() == "bearer" and hmac.compare_digest(supplied.strip().encode(), token.encode())


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    token = settings().metrics_token
    if not token:
        return JSONResponse(
            {"detail": "Métriques désactivées. Définissez METRICS_TOKEN."}, status_code=404
        )
    if not authorized(request, token):
        return JSONResponse(
            {"detail": "Jeton de métriques manquant ou invalide."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    with cache_lock:
        expires, body = cache.get("body", (0.0, ""))
        if expires <= time.monotonic():
            body = render()
            cache["body"] = (time.monotonic() + CACHE_SECONDS, body)
    return Response(
        body,
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
