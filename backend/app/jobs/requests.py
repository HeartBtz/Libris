"""Translation requests of the automation API: from the stored payload to a running pipeline.

Everything a request needs is in SQL (and its payload under DATA_DIR) before the API answers 202. A
request whose volume is busy stays `queued`; the worker's dispatcher ingests its chapters once no job
is active on the volume and starts its pipeline once no job holds it. Restarting the API or the worker
loses nothing: the dispatcher only reads SQL.
"""

import json
import logging
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.diagnostics import safe_trace
from app.engines.delivery.chapter_events import track_chapters
from app.engines.delivery.lifecycle import ENDED, fail, finalize, settle
from app.engines.ingestion.payload import PayloadRejected, TranslationPayload, payload_chapters
from app.engines.ingestion.store import Files, add_chapters, asset_file, conflicts, lock, read_asset
from app.engines.series.bible import refresh_series
from app.jobs.follow_up import follow_up_options
from app.jobs.launch import launch
from app.jobs.queue import ACTIVE, HELD, RUNNING
from app.models import Chapter, Job, Project, SourceAsset, TranslationRequest

logger = logging.getLogger("epub.requests")

LIVE = ("queued", "running")
FINISHED_JOBS = ("completed", "failed", "cancelled")


def volume_lock(db: Session, project_id: str) -> None:
    lock(db, f"api-volume:{project_id}")


def busy(db: Session, project: Project, statuses=ACTIVE) -> bool:
    return db.scalar(select(Job.id).where(Job.project_id == project.id, Job.status.in_(statuses)).limit(1)) is not None


def stored_payload(db: Session, request: TranslationRequest) -> TranslationPayload:
    asset = db.get(SourceAsset, request.options.get("asset_id") or "")
    data = read_asset(asset) if asset else None
    if data is None:
        raise ValueError("Le contenu enregistré de cette requête est introuvable.")
    return TranslationPayload.model_validate(json.loads(data))


def check_conflicts(db: Session, project: Project, payload: TranslationPayload) -> None:
    """Refuses at once a chapter that would conflict, even when its import has to wait."""
    if payload.replace_changed_chapters or project.source_format == "epub":
        return
    found = conflicts(db, project, payload_chapters(project, payload, settings().text_chapter_max_chars), set())
    if found:
        raise HTTPException(
            409,
            {
                "code": "chapter_conflict",
                "message": "Des chapitres existent déjà avec un autre contenu : envoyez "
                "« replace_changed_chapters: true » pour les remplacer.",
                "conflicts": found,
            },
        )


def ingest(db: Session, request: TranslationRequest, project: Project, payload: TranslationPayload, files: Files) -> None:
    chapters = payload_chapters(project, payload, settings().text_chapter_max_chars)
    replace = set(range(len(chapters))) if payload.replace_changed_chapters else set()
    # The payload is stored once for the request, not once per chapter: chapters are linked below.
    outcomes = add_chapters(db, project, chapters, files, replace=replace, discard_human=payload.discard_human)
    asset = db.get(SourceAsset, request.options.get("asset_id") or "")
    previous = set()
    for outcome in outcomes:
        if outcome.status == "unchanged" or asset is None:
            continue
        chapter = db.get(Chapter, outcome.chapter_id)
        if chapter.source_asset_id and chapter.source_asset_id != asset.id:
            previous.add(chapter.source_asset_id)
        chapter.source_asset_id = asset.id
    db.flush()
    for asset_id in previous:
        if not db.scalar(select(Chapter.id).where(Chapter.source_asset_id == asset_id).limit(1)):
            old = db.get(SourceAsset, asset_id)
            if old and not db.scalar(
                select(TranslationRequest.id).where(TranslationRequest.project_id == project.id,
                                                    TranslationRequest.status.in_(LIVE),
                                                    TranslationRequest.id != request.id).limit(1)
            ):  # fmt: skip
                path = asset_file(old)
                if path:
                    files.obsolete.append(path)
                db.delete(old)
    project.source_language = payload.source_language
    project.target_language = payload.target_language
    for key in ("provider_id", "quality", "context_backend"):
        value = getattr(payload.pipeline, key)
        if value is not None:
            setattr(project, key, value)
    if payload.author and not project.author:
        project.author = payload.author[:500]
    project.updated_at = time.time()
    counts = {status: sum(1 for o in outcomes if o.status == status) for status in ("created", "unchanged", "replaced")}
    request.chapter_ids = [outcome.chapter_id for outcome in outcomes]
    fresh = [outcome.chapter_id for outcome in outcomes if outcome.status != "unchanged"]
    request.options = {**request.options, "ingested": True, "chapters": counts, "new_chapter_ids": fresh}
    track_chapters(db, request)
    refresh_series(db, project.series_id)


def advance(db: Session, request: TranslationRequest, files: Files, payload: TranslationPayload | None = None) -> None:
    """Moves a queued request as far as its volume allows; the caller commits."""
    project = db.get(Project, request.project_id or "")
    if project is None:
        fail(db, request, "Le volume de cette requête a été supprimé.")
        return
    volume_lock(db, project.id)
    if not request.options.get("ingested"):
        if busy(db, project):
            return
        ingest(db, request, project, payload or stored_payload(db, request), files)
    if not request.options.get("start"):
        finalize(request, "imported")
        return
    if busy(db, project, HELD):
        return
    options = {"final_review": bool(request.options.get("final_review", True)), "translation_request": request.id}
    if request.options.get("input") != "epub":
        # Chapters sent to a volume already translated: only them (and anything unfinished) are worked on.
        options.update(follow_up_options(db, project, request.options.get("new_chapter_ids") or []))
    job, reason = launch(db, project, "pipeline", options)
    if job is None:
        fail(db, request, reason)
        return
    request.job_id, request.status, request.error = job.id, "running", ""


def failure_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        return str(detail.get("message", "")) if isinstance(detail, dict) else str(detail)
    if isinstance(exc, PayloadRejected):
        return "; ".join(str(item["msg"]) for item in exc.errors)
    return str(exc)


def dispatch() -> int:
    """One pass over the live requests; returns how many changed. Safe to run in several workers."""
    with SessionLocal() as db:
        ids = list(
            db.scalars(
                select(TranslationRequest.id)
                .where(TranslationRequest.status.in_(LIVE))
                .order_by(TranslationRequest.created_at)
                .limit(500)
            )
        )
    changed = 0
    for request_id in ids:
        files = Files()
        with SessionLocal() as db:
            request = db.scalar(
                select(TranslationRequest)
                .where(TranslationRequest.id == request_id, TranslationRequest.status.in_(LIVE))
                .with_for_update(skip_locked=True)
            )
            if request is None:
                continue
            before = (request.status, request.job_id)
            try:
                if request.status == "running":
                    settle(db, request)
                else:
                    advance(db, request, files)
                db.commit()
            except SQLAlchemyError:
                db.rollback()
                files.discard()
                raise
            except (HTTPException, ValueError) as exc:
                db.rollback()
                files.discard()
                request = db.get(TranslationRequest, request_id)
                fail(db, request, failure_message(exc)[:1500])
                db.commit()
            except Exception as exc:  # noqa: BLE001 - one broken request must not stop the others
                db.rollback()
                files.discard()
                logger.error("request=%s status=dispatch_failed trace=%s", request_id, safe_trace(exc))
                continue
            files.committed()
            changed += (request.status, request.job_id) != before
    return changed


def public_status(request: TranslationRequest, job: Job | None) -> str:
    """queued | imported | pending | running | paused | waiting | blocked | finalizing | completed |
    completed_with_residuals | failed | cancelled. Once the request ended, its own status is the answer;
    `finalizing`: the job ended and the result is being built."""
    if request.status in ENDED:
        return request.status
    if job is None:
        return request.status if request.status != "running" else "pending"
    if job.status in RUNNING:
        return "running"
    if job.status in FINISHED_JOBS:
        return "finalizing"
    return job.status
