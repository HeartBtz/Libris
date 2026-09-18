import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.common import row
from app.db import SessionLocal
from app.models import Event, Provider, RequestLog
from app.security import DB, Admin, CurrentUser, access, current_user

router = APIRouter(prefix="/api")


@router.get("/statistics/models")
def model_statistics(_admin: Admin, db: DB):
    # The model recorded with each request, not the provider's current one: editing a provider
    # must not move its history to another model.
    used = db.execute(
        select(
            RequestLog.model,
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
            func.coalesce(func.sum(RequestLog.completion_tokens), 0),
        ).group_by(RequestLog.model)
    ).all()
    configured = set(db.scalars(select(Provider.model))) - {model for model, *_ in used}
    rows = sorted([*used, *((model, 0, 0, 0) for model in configured)], key=lambda item: item[0])
    return [
        {
            "model": model,
            "requests": requests,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        for model, requests, input_tokens, output_tokens in rows
    ]


@router.get("/projects/{pid}/metrics")
def metrics(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user)
    values = db.execute(
        select(
            func.count(),
            func.sum(RequestLog.prompt_tokens),
            func.sum(RequestLog.completion_tokens),
            func.sum(RequestLog.duration),
            func.count().filter(RequestLog.status == "error"),
            func.count().filter(
                RequestLog.status == "running", RequestLog.created_at > time.time() - Provider.timeout - 30
            ),
            func.count().filter(RequestLog.cached.is_(True)),
        )
        .select_from(RequestLog)
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
        .where(RequestLog.project_id == pid)
    ).one()
    # The price recorded with the request; the provider's current price only for older rows.
    cost = db.scalar(
        select(
            func.sum(
                (
                    RequestLog.prompt_tokens
                    * func.coalesce(RequestLog.input_cost, Provider.input_cost, 0)
                    + RequestLog.completion_tokens
                    * func.coalesce(RequestLog.output_cost, Provider.output_cost, 0)
                )
                / 1_000_000
            )
        )
        .outerjoin(Provider, RequestLog.provider_id == Provider.id)
        .where(RequestLog.project_id == pid)
    )
    return dict(
        zip(
            ("requests", "input_tokens", "output_tokens", "duration", "errors", "active", "cache_hits"),
            [v or 0 for v in values],
        ),
        cost=cost or 0,
    )


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


@router.get("/projects/{pid}/events")
def events(pid: str, request: Request, user: CurrentUser, db: DB, after: int | None = Query(None, ge=0)):
    access(db, pid, user)
    last_id = stream_cursor(db, pid, after, request.headers.get("Last-Event-ID"))
    db.close()

    async def stream():
        nonlocal last_id
        while not await request.is_disconnected():
            with SessionLocal() as session:
                try:
                    account = current_user(session, request.cookies.get("epub_session"))
                    access(session, pid, account)
                except HTTPException:
                    return
                rows = list(
                    session.scalars(
                        select(Event)
                        .where(Event.project_id == pid, Event.id > last_id)
                        .order_by(Event.id)
                        .limit(100)
                    )
                )
            for event in rows:
                last_id = event.id
                yield f"id: {event.id}\ndata: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
            if not rows:
                yield ": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
