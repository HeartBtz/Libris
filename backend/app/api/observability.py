import asyncio
import json
import threading
import time
from collections import Counter
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.common import row
from app.api.monitoring import WASTED_STATUSES
from app.config import settings
from app.db import SessionLocal
from app.maintenance.usage import usage
from app.models import Event, Provider, RequestLog
from app.security import DB, Admin, CurrentUser, access, current_user

router = APIRouter(prefix="/api")


@router.get("/statistics/models")
def model_statistics(_admin: Admin, db: DB):
    # The model recorded with each request, not the provider's current one: editing a provider
    # must not move its history to another model. Read from the daily aggregates (app.maintenance.usage).
    by_model: dict[str, list[int]] = {}
    for model, status, requests, prompt, completion, _duration, _cost in usage(db, ("model", "status")):
        current = by_model.setdefault(model, [0, 0, 0, 0])
        current[0] += requests
        current[1] += prompt
        current[2] += completion
        current[3] += prompt if status in WASTED_STATUSES else 0
    used = [(model, *values) for model, values in by_model.items()]
    configured = set(db.scalars(select(Provider.model))) - {model for model, *_ in used}
    rows = sorted([*used, *((model, 0, 0, 0, 0) for model in configured)], key=lambda item: item[0])
    return [
        {
            "model": model,
            "requests": requests,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "wasted_input_tokens": wasted,
            "wasted_share": wasted / input_tokens if input_tokens else 0,
        }
        for model, requests, input_tokens, output_tokens, wasted in rows
    ]


@router.get("/projects/{pid}/metrics")
def metrics(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user)
    result = dict.fromkeys(("requests", "input_tokens", "output_tokens", "duration", "errors", "cache_hits"), 0)
    result.update(wasted_input_tokens=0, cost=0)
    # Daily aggregates plus the requests not rolled up yet; the cost at the price recorded with each request.
    for status, cached, requests, prompt, completion, duration, cost in usage(
        db, ("status", "cached"), ("project_id", [pid])
    ):
        result["requests"] += requests
        result["input_tokens"] += prompt
        result["output_tokens"] += completion
        result["duration"] += duration
        result["cost"] += cost
        result["errors"] += requests if status == "error" else 0
        result["cache_hits"] += requests if cached else 0
        result["wasted_input_tokens"] += prompt if status in WASTED_STATUSES else 0
    result["active"] = db.scalar(
        select(func.count())
        .select_from(RequestLog)
        .join(Provider, RequestLog.provider_id == Provider.id)
        .where(
            RequestLog.project_id == pid,
            RequestLog.status == "running",
            RequestLog.created_at > time.time() - Provider.timeout - 30,
        )
    )
    # Input spent on calls whose answer was never applied: errors, refusals, interruptions.
    spent = result["input_tokens"]
    result["wasted_share"] = result["wasted_input_tokens"] / spent if spent else 0
    return result


@router.get("/projects/{pid}/requests")
def requests(pid: str, user: CurrentUser, db: DB, offset: int = Query(0, ge=0)):
    access(db, pid, user)
    return [
        row(r, ("messages", "raw", "parsed", "context", "parameters"))
        for r in db.scalars(
            select(RequestLog)
            .where(RequestLog.project_id == pid)
            .order_by(RequestLog.created_at.desc())
            .offset(offset)
            .limit(100)
        )
    ]


@router.get("/requests/{rid}")
def request(rid: str, user: CurrentUser, db: DB):
    log = db.get(RequestLog, rid)
    if not log:
        raise HTTPException(404, "Requête introuvable.")
    access(db, log.project_id, user)
    return row(log)


def stream_cursor(db, pid: str, after: int | None, resumed: str | None) -> int:
    """Where a project event stream starts.

    A reconnecting client resumes after the last event it saw; `after` asks for an explicit replay.
    A fresh client has just loaded the current state over REST: it only needs what happens next,
    not the thousands of past events of a finished book.
    """
    if after is None and resumed is None:
        return db.scalar(select(func.max(Event.id)).where(Event.project_id == pid)) or 0
    try:
        return max(after or 0, int(resumed or 0))
    except ValueError:
        raise HTTPException(422, "Identifiant d’événement invalide.") from None


class StreamSlots:
    """Open event streams, per account and for the whole process.

    Each stream holds a connection and polls the database: without a bound, one account opening
    many tabs (or a script) could hold every worker thread and connection of the API.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.by_user: Counter[str] = Counter()

    def acquire(self, user_id: str) -> Callable[[], None]:
        limits = settings()
        with self.lock:
            if self.by_user[user_id] >= limits.event_streams_per_user:
                raise HTTPException(
                    429,
                    f"Trop de suivis en direct ouverts pour ce compte ({limits.event_streams_per_user} au "
                    "maximum). Fermez des onglets Libris, puis rechargez la page.",
                )
            if self.by_user.total() >= limits.event_streams_total:
                raise HTTPException(
                    429, "Le serveur suit déjà trop de livres en direct. Réessayez dans quelques instants."
                )
            self.by_user[user_id] += 1
        released = False

        def release() -> None:
            nonlocal released
            with self.lock:
                if not released:
                    released = True
                    self.by_user[user_id] -= 1
                    if self.by_user[user_id] <= 0:
                        del self.by_user[user_id]

        return release


streams = StreamSlots()


class BoundedStream(StreamingResponse):
    """A streaming response that gives its slot back however the connection ends."""

    def __init__(self, content, release: Callable[[], None], **options):
        super().__init__(content, **options)
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


def new_events(pid: str, token: str | None, last_id: int) -> list[tuple[int, dict]] | None:
    """Events after `last_id`, or None once the account may no longer read the book."""
    with SessionLocal() as session:
        try:
            access(session, pid, current_user(session, token))
        except HTTPException:
            return None
        return [
            (event.id, event.payload)
            for event in session.scalars(
                select(Event).where(Event.project_id == pid, Event.id > last_id).order_by(Event.id).limit(100)
            )
        ]


@router.get("/projects/{pid}/events")
def events(pid: str, request: Request, user: CurrentUser, db: DB, after: int | None = Query(None, ge=0)):
    access(db, pid, user)
    last_id = stream_cursor(db, pid, after, request.headers.get("Last-Event-ID"))
    db.close()
    release = streams.acquire(user.id)
    token = request.cookies.get("epub_session")

    async def stream():
        nonlocal last_id
        while not await request.is_disconnected():
            # Database calls are synchronous: run them off the event loop that serves every request.
            rows = await asyncio.to_thread(new_events, pid, token, last_id)
            if rows is None:
                return
            for event_id, payload in rows:
                last_id = event_id
                yield f"id: {event_id}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return BoundedStream(
        stream(),
        release,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
