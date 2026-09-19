"""`chapters.translated`: progress webhooks of a request, one per batch of chapters newly translated.

A request that lists `chapters.translated` in `callback_events` records, when its chapters are
imported, those still missing a translation. While its job runs, each pass of the request dispatcher
(and each status read that settles the request) looks for the ones now fully translated and queues one
event for them: a batch. The worker sends batches with the same allow-list, signature and retries as
the final `translation_request.finished` webhook (app.engines.delivery.webhooks). The text of a batch
is a draft until the request ends: the final review may still improve it.
"""

import json
import logging
import time

import httpx
from sqlalchemy import func, select

from app.automation_settings import webhook_config
from app.db import SessionLocal
from app.engines.delivery import webhooks
from app.models import Chapter, Segment, TranslationRequest, WebhookEvent

logger = logging.getLogger("epub.webhooks")
EVENT = "chapters.translated"
BATCH = 20


def wanted(request: TranslationRequest) -> bool:
    return bool(request.callback_url) and EVENT in (request.options.get("callback_events") or [])


def untranslated(db, chapter_ids: list[str]) -> set[str]:
    """Chapters without passages, or with a passage still missing its translation."""
    if not chapter_ids:
        return set()
    with_text = set(
        db.scalars(select(Segment.chapter_id).where(Segment.chapter_id.in_(chapter_ids)).distinct())
    )
    missing = set(
        db.scalars(
            select(Segment.chapter_id)
            .where(Segment.chapter_id.in_(chapter_ids), Segment.translation == "")
            .distinct()
        )
    )
    return {chapter_id for chapter_id in chapter_ids if chapter_id not in with_text or chapter_id in missing}


def track_chapters(db, request: TranslationRequest) -> None:
    """Called once the request's chapters exist: remembers those a batch will announce."""
    if not wanted(request):
        return
    pending = untranslated(db, list(request.chapter_ids or []))
    request.options = {
        **request.options,
        "unannounced": [chapter_id for chapter_id in request.chapter_ids or [] if chapter_id in pending],
    }


def announce(db, request: TranslationRequest) -> WebhookEvent | None:
    """Queues one batch for the chapters translated since the last one; the caller commits."""
    waiting = list(request.options.get("unannounced") or [])
    if not waiting or not wanted(request):
        return None
    existing = set(db.scalars(select(Chapter.id).where(Chapter.id.in_(waiting))))
    still = untranslated(db, [chapter_id for chapter_id in waiting if chapter_id in existing])
    ready = [chapter_id for chapter_id in waiting if chapter_id in existing and chapter_id not in still]
    remaining = [chapter_id for chapter_id in waiting if chapter_id in still]
    if remaining != waiting:
        request.options = {**request.options, "unannounced": remaining}
    if not ready:
        return None
    sequence = (
        db.scalar(select(func.max(WebhookEvent.sequence)).where(WebhookEvent.request_id == request.id)) or 0
    ) + 1
    event = WebhookEvent(request_id=request.id, event=EVENT, sequence=sequence, chapter_ids=ready, state="pending",
                         attempts=0, next_attempt=0, error="")  # fmt: skip
    db.add(event)
    db.flush()
    logger.info("request=%s event=%s batch=%s chapters=%s", request.id, EVENT, sequence, len(ready))
    return event


def body(db, event: WebhookEvent, request: TranslationRequest) -> dict:
    base = f"/api/v1/translation-requests/{request.id}"
    chapters = {c.id: c for c in db.scalars(select(Chapter).where(Chapter.id.in_(event.chapter_ids or [])))}
    wanted_total = len(request.chapter_ids or [])
    announced = sum(
        len(ids)
        for ids in db.scalars(
            select(WebhookEvent.chapter_ids).where(
                WebhookEvent.request_id == request.id, WebhookEvent.sequence <= event.sequence
            )
        )
    )
    return {
        "event": EVENT,
        "request_id": request.id,
        "external_id": request.external_id,
        "series_id": request.series_id,
        "project_id": request.project_id,
        "batch": event.sequence,
        "chapters": [
            {
                "chapter_id": chapter.id,
                "external_id": chapter.external_id,
                "number": chapter.chapter_number,
                "title": chapter.title,
            }
            for chapter in sorted(chapters.values(), key=lambda c: c.position)
        ],
        "announced": announced,
        "total": wanted_total,
        "status_url": base,
        "result_url": f"{base}/result?partial=true",
        "created_at": event.created_at,
    }


def deliver_event(event_id: str, now: float | None = None) -> str | None:
    """One attempt for one batch; returns its new state (None when not due)."""
    now = now or time.time()
    with SessionLocal() as db:
        query = select(WebhookEvent).where(
            WebhookEvent.id == event_id, WebhookEvent.state == "pending", WebhookEvent.next_attempt <= now
        )
        if db.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        event = db.scalar(query)
        if event is None:
            return None
        request = db.get(TranslationRequest, event.request_id)
        if request is None or not request.callback_url:
            event.state, event.error = "failed", "Aucune adresse de rappel."
            db.commit()
            return event.state
        content = json.dumps(body(db, event, request), ensure_ascii=False, separators=(",", ":")).encode()
        timestamp = str(int(now))
        secret = webhooks.signing_secret(db, request)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Libris-Webhook/1",
            "X-Libris-Event": event.event,
            "X-Libris-Delivery": f"{request.id}:{event.event}:{event.sequence}:{event.attempts + 1}",
            "X-Libris-Timestamp": timestamp,
        }
        error = ""
        if not secret:
            error = "Aucun secret de signature disponible."
        else:
            headers["X-Libris-Signature"] = webhooks.signature(secret, timestamp, content)
            try:
                status = webhooks.send(request.callback_url, content, headers)
                if not 200 <= status < 300:
                    error = f"HTTP {status}"
            except webhooks.WebhookRefused as exc:
                error = str(exc)
            except httpx.HTTPError as exc:
                error = type(exc).__name__
        event.attempts += 1
        if not error:
            event.state, event.error, event.delivered_at = "delivered", "", now
        elif event.attempts >= webhook_config(db)["max_attempts"]:
            event.state, event.error = "failed", error[:500]
        else:
            event.error = error[:500]
            event.next_attempt = now + webhooks.backoff(event.attempts)
        logger.info(
            "request=%s event=%s batch=%s webhook=%s attempt=%s",
            request.id, event.event, event.sequence, event.state, event.attempts,
        )  # fmt: skip
        state = event.state
        db.commit()
        return state


def pump_events(now: float | None = None) -> int:
    """One pass over the batches due, oldest first; returns how many calls were attempted."""
    now = now or time.time()
    with SessionLocal() as db:
        due = list(
            db.scalars(
                select(WebhookEvent.id)
                .where(WebhookEvent.state == "pending", WebhookEvent.next_attempt <= now)
                .order_by(WebhookEvent.next_attempt, WebhookEvent.created_at, WebhookEvent.sequence)
                .limit(BATCH)
            )
        )
    return sum(1 for event_id in due if deliver_event(event_id, now) is not None)


def summary(db, request: TranslationRequest) -> dict:
    """What the status document shows about the batches of a request."""
    counts = dict(
        db.execute(
            select(WebhookEvent.state, func.count())
            .where(WebhookEvent.request_id == request.id)
            .group_by(WebhookEvent.state)
        ).all()
    )
    last = db.scalar(
        select(WebhookEvent.error)
        .where(WebhookEvent.request_id == request.id, WebhookEvent.error != "")
        .order_by(WebhookEvent.sequence.desc())
        .limit(1)
    )
    return {
        "event": EVENT,
        "batches": sum(counts.values()),
        "delivered": counts.get("delivered", 0),
        "pending": counts.get("pending", 0),
        "failed": counts.get("failed", 0),
        "waiting_chapters": len(request.options.get("unannounced") or []),
        "error": last or "",
    }
