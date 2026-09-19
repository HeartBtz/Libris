"""Automation API (`/api/v1`): translation requests authenticated by API tokens.

Separate from the interface's API: no session cookie, versioned paths, errors shaped as
`{"detail": {"code", "message", ...}}`. A request is a JSON document, an EPUB, or TXT chapter files;
it is committed to SQL before the 202 answer, the pipeline runs in the worker, and the request always
ends: completed, completed_with_residuals (some passages kept their source, listed in the report),
failed (with the reason) or cancelled. The client polls the status (or long-polls with `?wait=`), then
fetches the result; an optional webhook, restricted to hosts an administrator allowed, is sent by the
worker when the request ends (app.engines.delivery.webhooks).
"""

import asyncio
import hashlib
import time
import zipfile
from dataclasses import dataclass, field
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.api.projects import control_job, import_book
from app.api.providers import SHARED_FIELDS
from app.api.tokens import Caller, require
from app.config import API_RESULT_WAIT_CEILING, settings
from app.db import SessionLocal
from app.engines.budget import refuse_token_request
from app.engines.delivery.epub import DeliveryFailed
from app.engines.delivery.intake import UploadOptions, epub_digest, text_payload, upload_options
from app.engines.delivery.lifecycle import ENDED, SUCCESS, finalize, refresh, settle
from app.engines.delivery.results import FORMATS, MEDIA_TYPES, default_format, render, request_texts, stored
from app.engines.delivery.webhooks import WebhookRefused, checked_url, require_signing
from app.engines.exports.text import TEXT_KINDS
from app.engines.ingestion.naming import volume_from_name
from app.engines.ingestion.payload import (
    PayloadRejected,
    SeriesReference,
    TranslationPayload,
    parse_payload,
    payload_asset,
)
from app.engines.ingestion.store import (
    Files,
    attach,
    epub_duplicate,
    find_series,
    get_or_create_series,
    lock,
    primary_asset,
    safe_display_name,
    store_asset,
)
from app.i18n import english, preferred_language
from app.jobs.queue import HELD
from app.jobs.requests import advance, busy, check_conflicts, public_status, volume_lock
from app.models import (
    Chapter,
    Job,
    Project,
    Provider,
    Segment,
    Series,
    SourceAsset,
    TranslationRequest,
)
from app.progress import project_progress
from app.security import DB

router = APIRouter(prefix="/api/v1")
MAX_KEY = 200
FLAGGED = ("check", "error", "refused")
POLL_SECONDS = 1.0
UPLOAD_FIELDS = ("file", "files")
ACCEPTED = {
    "application/epub+zip": "epub",
    "application/zip": "txt-zip",
    "text/plain": "txt",
    "application/json": "json",
}


def links(request: TranslationRequest) -> dict:
    base = f"/api/v1/translation-requests/{request.id}"
    return {"status_url": base, "result_url": f"{base}/result"}


def summary(db, request: TranslationRequest) -> dict:
    job = db.get(Job, request.job_id) if request.job_id else None
    return {
        "request_id": request.id,
        "external_id": request.external_id,
        "series_id": request.series_id,
        "project_id": request.project_id,
        "job_id": request.job_id,
        "input": request.options.get("input", "json"),
        "status": public_status(request, job),
        **links(request),
    }


def existing(db, owner_id: str, key: str | None, external_id: str | None) -> list[TranslationRequest]:
    found = []
    if key:
        found += db.scalars(
            select(TranslationRequest).where(
                TranslationRequest.owner_id == owner_id, TranslationRequest.idempotency_key == key
            )
        )
    if external_id:
        found += db.scalars(
            select(TranslationRequest).where(
                TranslationRequest.owner_id == owner_id, TranslationRequest.external_id == external_id
            )
        )
    return list({request.id: request for request in found}.values())


def replayed(found: list[TranslationRequest], digest: str) -> TranslationRequest:
    if len(found) > 1 or found[0].payload_sha256 != digest:
        raise HTTPException(
            409,
            {
                "code": "idempotency_conflict",
                "message": "Une autre requête a déjà utilisé cette clé d’idempotence ou cet external_id "
                "avec un contenu différent.",
                "request_id": found[0].id,
            },
        )
    return found[0]


def target_series(
    db, owner_id: str, reference: SeriesReference, source_language: str | None, target_language: str | None,
    provider_id: str | None, quality: str | None, context_backend: str | None,
) -> Series:  # fmt: skip
    if reference.id:
        series = db.get(Series, reference.id)
        if not series or series.owner_id != owner_id:
            raise HTTPException(404, {"code": "series_not_found", "message": "Série introuvable."})
    else:
        series = find_series(db, owner_id, reference.name or "")
        if series is None:
            if not reference.create_if_missing:
                raise HTTPException(404, {"code": "series_not_found", "message": "Série introuvable."})
            series = get_or_create_series(
                db, owner_id, reference.name or "", "books",
                source_language=source_language, target_language=target_language,
                provider_id=provider_id, quality=quality, context_backend=context_backend,
            )  # fmt: skip
    if series.archived_at:
        raise HTTPException(
            409, {"code": "series_archived",
                  "message": "Cette série est archivée : restaurez-la avant d’y ajouter du contenu."},
        )  # fmt: skip
    return series


def payload_series(db, owner_id: str, payload: TranslationPayload) -> Series:
    pipeline = payload.pipeline
    return target_series(
        db, owner_id, payload.series, payload.source_language, payload.target_language,
        pipeline.provider_id, pipeline.quality, pipeline.context_backend,
    )  # fmt: skip


def target_volume(db, owner_id: str, series: Series, payload: TranslationPayload) -> Project:
    """Found by (series, volume external_id), then (series, number); otherwise created."""
    volume = payload.volume
    lock(db, f"api-volume-number:{series.id}:{volume.number}")
    project = None
    if volume.external_id:
        project = db.scalar(
            select(Project).where(Project.series_id == series.id, Project.external_id == volume.external_id)
        )
    if project is None:
        project = db.scalar(
            select(Project)
            .where(Project.series_id == series.id, Project.volume_number == volume.number)
            .order_by(Project.created_at)
            .limit(1)
        )
        if project and volume.external_id and project.external_id and project.external_id != volume.external_id:
            raise HTTPException(
                409, {"code": "volume_conflict",
                      "message": f"Le volume {volume.number} de cette série a un autre identifiant externe."},
            )  # fmt: skip
    if project is not None:
        if project.archived_at is not None:
            raise HTTPException(
                409, {"code": "volume_archived",
                      "message": "Ce volume est archivé : restaurez-le avant d’y ajouter des chapitres."},
            )  # fmt: skip
        if project.source_format == "epub":
            raise HTTPException(
                409, {"code": "volume_conflict",
                      "message": "Ce volume vient d’un EPUB : ajoutez les chapitres texte à un autre volume."},
            )  # fmt: skip
        if volume.external_id and not project.external_id:
            project.external_id = volume.external_id
        return project
    project = Project(
        owner_id=owner_id,
        title=(volume.title.strip() or f"{series.name} — {volume.number}")[:500],
        author=payload.author[:500],
        source_language=payload.source_language,
        target_language=payload.target_language,
        volume_number=volume.number,
        source_format="json",
        project_kind="volume",
        external_id=volume.external_id,
        book_info={},
        import_meta={"version": 1, "adapter": "json"},
        original_hash="",
        original_path="",
    )
    attach(project, series)
    for key in ("provider_id", "quality", "context_backend"):
        value = getattr(payload.pipeline, key) or getattr(series, key)
        if value:
            setattr(project, key, value)
    db.add(project)
    db.flush()
    return project


def check_start(db, caller: Caller, start: bool, provider_id: str | None) -> None:
    if start and "pipeline:start" not in caller.token.scopes:
        raise HTTPException(
            403, {"code": "insufficient_scope", "scope": "pipeline:start",
                  "message": "Ce jeton n’a pas la permission « pipeline:start »."},
        )  # fmt: skip
    if provider_id and not db.get(Provider, provider_id):
        raise HTTPException(422, {"code": "unknown_provider", "message": "Provider inconnu."})
    refuse_token_request(db, caller.token, start)  # 402 once the token's cost budget is reached


def check_callback(caller: Caller, url: str | None) -> None:
    """Refused at once, before anything is stored: the client learns why its webhook cannot work."""
    if not url:
        return
    try:
        checked_url(url)
        require_signing(caller.token)
    except WebhookRefused as exc:
        raise HTTPException(422, {"code": "callback_refused", "message": str(exc)}) from None


def need_provider(start: bool, project: Project) -> None:
    if start and not project.provider_id:
        raise HTTPException(
            422, {"code": "provider_required",
                  "message": "Aucun provider pour ce volume : indiquez pipeline.provider_id."},
        )  # fmt: skip


def create_request(
    db, caller: Caller, payload: TranslationPayload, key: str | None, decisions: list[dict] | None = None,
    input_kind: str = "json",
) -> tuple[TranslationRequest, bool]:  # fmt: skip
    owner_id = caller.user.id
    data = payload_asset(payload)
    digest = hashlib.sha256(data.data).hexdigest()
    for name in (f"key:{key}" if key else "", f"external:{payload.external_id}" if payload.external_id else ""):
        if name:
            lock(db, f"api-request:{owner_id}:{name}")
    found = existing(db, owner_id, key, payload.external_id)
    if found:
        return replayed(found, digest), True
    check_start(db, caller, payload.pipeline.start, payload.pipeline.provider_id)
    check_callback(caller, payload.callback_url)
    files = Files()
    try:
        series = payload_series(db, owner_id, payload)
        project = target_volume(db, owner_id, series, payload)
        if payload.pipeline.start and not (payload.pipeline.provider_id or project.provider_id):
            need_provider(True, project)
        volume_lock(db, project.id)
        asset = db.scalar(
            select(SourceAsset).where(
                SourceAsset.project_id == project.id, SourceAsset.format == "json", SourceAsset.sha256 == digest
            ).limit(1)
        ) or store_asset(db, project, data, files)
        request = TranslationRequest(
            owner_id=owner_id,
            token_id=caller.token.id,
            external_id=payload.external_id,
            idempotency_key=key,
            payload_sha256=digest,
            series_id=series.id,
            project_id=project.id,
            status="queued",
            callback_url=payload.callback_url,
            options={
                "input": input_kind,
                "asset_id": asset.id,
                "start": payload.pipeline.start,
                "final_review": payload.pipeline.final_review,
                "output_format": payload.output.format,
                "ingested": False,
                "decisions": decisions or [],
            },
            chapter_ids=[],
        )
        db.add(request)
        db.flush()
        # Checked now even when the import has to wait for the volume: the client learns it at once.
        check_conflicts(db, project, payload)
        advance(db, request, files, payload)
        db.commit()
    except PayloadRejected as exc:
        db.rollback()
        files.discard()
        raise HTTPException(422, {"code": "invalid_payload", "message": str(exc), "errors": exc.errors}) from None
    except IntegrityError:
        # Two identical requests at once (SQLite, or keys the advisory locks did not cover).
        db.rollback()
        files.discard()
        found = existing(db, owner_id, key, payload.external_id)
        if not found:
            raise
        return replayed(found, digest), True
    except BaseException:
        db.rollback()
        files.discard()
        raise
    files.committed()
    return request, False


def next_volume_number(db, series: Series, name: str) -> tuple[int, dict]:
    """The number the file name gives when it is free, otherwise the one after the last volume."""
    taken = set(db.scalars(select(Project.volume_number).where(Project.series_id == series.id)))
    guess = volume_from_name(name)
    if guess.value is not None and int(guess.value) not in taken:
        return int(guess.value), {"volume_number": int(guess.value), "confidence": guess.confidence,
                                  "reason": f"numéro de volume lu dans le nom du fichier : {guess.reason}"}  # fmt: skip
    number = max((n for n in taken if n is not None), default=0) + 1
    return number, {"volume_number": number, "confidence": "low",
                    "reason": "aucun numéro libre dans le nom du fichier : volume suivant de la série"}  # fmt: skip


def epub_volume(
    db, owner_id: str, series: Series | None, data: bytes, name: str, options: UploadOptions, files: Files,
) -> tuple[Project, list[dict]]:  # fmt: skip
    """The volume of an uploaded EPUB: the one already made from the same file, or a new one."""
    decisions: list[dict] = []
    same = epub_duplicate(db, owner_id, hashlib.sha256(data).hexdigest())
    if same is not None:
        if same.archived_at is not None:
            raise HTTPException(
                409, {"code": "volume_archived",
                      "message": "Ce volume est archivé : restaurez-le avant d’y ajouter des chapitres."},
            )  # fmt: skip
        decisions.append({"project_id": same.id, "reason": "EPUB déjà dans la bibliothèque : son volume est repris"})
        return same, decisions
    number = options.volume
    if series is not None:
        if number is None:
            number, decision = next_volume_number(db, series, name)
            decisions.append(decision)
        lock(db, f"api-volume-number:{series.id}:{number}")
        if db.scalar(select(Project.id).where(Project.series_id == series.id, Project.volume_number == number)):
            raise HTTPException(
                409, {"code": "volume_conflict",
                      "message": f"Le volume {number} de cette série existe déjà avec un autre contenu."},
            )  # fmt: skip
    try:
        project = import_book(db, owner_id, data, name=name, series=series,
                              volume_number=number if series else None, files=files)  # fmt: skip
    except (ValueError, KeyError, zipfile.BadZipFile, etree.LxmlError) as exc:
        raise HTTPException(
            422, {"code": "invalid_epub", "message": f"Cet EPUB ne peut pas être lu : {str(exc)[:300]}"}
        ) from None
    return project, decisions


def apply_upload_options(db, project: Project, options: UploadOptions, decisions: list[dict]) -> None:
    if busy(db, project, HELD):
        # A job already works on this volume: its settings are not changed under it.
        decisions.append({"project_id": project.id, "reason": "volume occupé : ses réglages actuels sont gardés"})
        return
    for key in ("source_language", "target_language", "provider_id", "quality", "context_backend"):
        value = getattr(options, key)
        if value is not None:
            setattr(project, key, value)
    if options.title.strip():
        project.title = options.title.strip()[:500]
    if options.author.strip():
        project.author = options.author.strip()[:500]
    if options.volume_external_id and not project.external_id:
        project.external_id = options.volume_external_id


def create_epub_request(
    db, caller: Caller, data: bytes, name: str, options: UploadOptions, key: str | None
) -> tuple[TranslationRequest, bool]:
    owner_id = caller.user.id
    digest = epub_digest(data, options)
    for label in (f"key:{key}" if key else "", f"external:{options.external_id}" if options.external_id else ""):
        if label:
            lock(db, f"api-request:{owner_id}:{label}")
    found = existing(db, owner_id, key, options.external_id)
    if found:
        return replayed(found, digest), True
    check_start(db, caller, options.start, options.provider_id)
    check_callback(caller, options.callback_url)
    files = Files()
    try:
        series = None
        if options.series or options.series_id:
            reference = SeriesReference(id=options.series_id, name=options.series)
            series = target_series(
                db, owner_id, reference, options.source_language, options.target_language, options.provider_id,
                options.quality, options.context_backend,
            )  # fmt: skip
        project, decisions = epub_volume(db, owner_id, series, data, name, options, files)
        apply_upload_options(db, project, options, decisions)
        need_provider(options.start, project)
        volume_lock(db, project.id)
        asset = primary_asset(db, project)
        chapters = list(
            db.scalars(
                select(Chapter.id)
                .where(Chapter.project_id == project.id, Chapter.kind.in_(TEXT_KINDS))
                .order_by(Chapter.position)
            )
        )
        request = TranslationRequest(
            owner_id=owner_id,
            token_id=caller.token.id,
            external_id=options.external_id,
            idempotency_key=key,
            payload_sha256=digest,
            series_id=series.id if series else project.series_id,
            project_id=project.id,
            status="queued",
            callback_url=options.callback_url,
            options={
                "input": "epub",
                "asset_id": asset.id if asset else None,
                "start": options.start,
                "final_review": options.final_review,
                "output_format": options.output_format or "epub",
                "ingested": True,
                "chapters": {"created": len(chapters), "unchanged": 0, "replaced": 0},
                "decisions": decisions,
            },
            chapter_ids=chapters,
        )
        db.add(request)
        db.flush()
        advance(db, request, files)
        db.commit()
    except IntegrityError:
        db.rollback()
        files.discard()
        found = existing(db, owner_id, key, options.external_id)
        if not found:
            raise
        return replayed(found, digest), True
    except BaseException:
        db.rollback()
        files.discard()
        raise
    files.committed()
    return request, False


@dataclass
class Submission:
    """What a POST carried: a JSON request (possibly made from TXT files) or an EPUB and its options."""

    payload: TranslationPayload | None = None
    epub: bytes | None = None
    name: str = "book.epub"
    options: UploadOptions | None = None
    decisions: list[dict] = field(default_factory=list)
    kind: str = "json"


def submit(db, caller: Caller, submission: Submission, key: str | None) -> tuple[dict, bool]:
    if submission.epub is not None:
        request, again = create_epub_request(db, caller, submission.epub, submission.name, submission.options, key)
    else:
        request, again = create_request(db, caller, submission.payload, key, submission.decisions, submission.kind)
    return summary(db, request), again


def too_large() -> HTTPException:
    return HTTPException(
        413, {"code": "payload_too_large",
              "message": f"Requête trop volumineuse : {settings().api_payload_mb} Mo au maximum."},
    )  # fmt: skip


def invalid(message: str) -> HTTPException:
    return HTTPException(422, {"code": "invalid_payload", "message": message})


def rejected(exc: PayloadRejected) -> HTTPException:
    return HTTPException(422, {"code": "invalid_payload", "message": str(exc), "errors": exc.errors})


async def read_upload(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise too_large()
    return data


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].casefold() if "." in name else ""


async def read_multipart(request: Request, limit: int) -> Submission:
    form = await request.form(max_files=settings().api_max_chapters + 1, max_fields=50)
    uploads, values = [], {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            if key not in UPLOAD_FIELDS:
                raise invalid("Envoyez les fichiers dans le champ « file » (ou « files »).")
            uploads.append(value)
        else:
            values[key] = value
    kinds = {extension(upload.filename or "") for upload in uploads}
    if not uploads or len(kinds) != 1 or not kinds <= {"json", "epub", "txt"}:
        raise invalid("Envoyez un fichier JSON, un EPUB, ou des chapitres .txt (un seul type par requête).")
    kind = kinds.pop()
    if kind in {"json", "epub"} and len(uploads) != 1:
        raise invalid("Envoyez un seul fichier JSON ou EPUB par requête.")
    if kind == "json":
        if values:
            raise invalid("Envoyez un seul fichier JSON dans le champ « file ».")
        return Submission(payload=parse(await read_upload(uploads[0], limit)))
    try:
        options = upload_options(values)
    except PayloadRejected as exc:
        raise rejected(exc) from None
    if kind == "epub":
        data = await read_upload(uploads[0], limit)
        return Submission(epub=data, name=safe_display_name(uploads[0].filename or "book.epub"), options=options,
                          kind="epub")  # fmt: skip
    files, total = [], 0
    for upload in uploads:
        data = await read_upload(upload, limit)
        total += len(data)
        if total > limit:
            raise too_large()
        files.append((safe_display_name(upload.filename or "chapitre.txt"), data))
    try:
        payload, decisions = text_payload(files, options, settings().api_max_chapters)
    except PayloadRejected as exc:
        raise rejected(exc) from None
    return Submission(payload=payload, decisions=decisions, kind="txt")


def parse(data: bytes) -> TranslationPayload:
    try:
        return parse_payload(data, settings().api_max_chapters)
    except PayloadRejected as exc:
        raise rejected(exc) from None


async def read_input(request: Request) -> Submission:
    kind = request.headers.get("content-type", "").split(";")[0].strip().casefold()
    limit = settings().api_payload_mb * 1024**2
    if kind == "application/json":
        data = await request.body()
        if len(data) > limit:
            raise too_large()
        return Submission(payload=parse(data))
    if kind == "multipart/form-data":
        return await read_multipart(request, limit)
    if kind == "application/epub+zip":
        values = dict(request.query_params)
        name = safe_display_name(values.pop("filename", "") or "book.epub")
        try:
            options = upload_options(values)
        except PayloadRejected as exc:
            raise rejected(exc) from None
        data = await request.body()
        if len(data) > limit:
            raise too_large()
        return Submission(epub=data, name=name, options=options, kind="epub")
    raise HTTPException(
        415, {"code": "unsupported_media_type",
              "message": "Envoyez application/json, application/epub+zip, ou des fichiers (.json, .epub ou .txt) "
                         "en multipart/form-data."},
    )  # fmt: skip


def idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or len(value) > MAX_KEY or not value.isprintable():
        raise HTTPException(
            422, {"code": "invalid_idempotency_key",
                  "message": f"En-tête Idempotency-Key invalide : 1 à {MAX_KEY} caractères imprimables."},
        )  # fmt: skip
    return value


@router.post("/translation-requests", status_code=202)
async def create_translation_request(
    request: Request,
    caller: Annotated[Caller, Depends(require("content:write"))],
    db: DB,
    idempotency: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = idempotency_key(idempotency)
    submission = await read_input(request)
    body, again = await run_in_threadpool(submit, db, caller, submission, key)
    headers = {"Location": body["status_url"]}
    if again:
        headers["Idempotent-Replayed"] = "true"
    return JSONResponse(body, status_code=200 if again else 202, headers=headers)


def owned_request(db, request_id: str, caller: Caller) -> TranslationRequest:
    found = db.get(TranslationRequest, request_id)
    if not found or found.owner_id != caller.user.id:
        raise HTTPException(404, {"code": "request_not_found", "message": "Requête de traduction introuvable."})
    return found


def chapter_rows(db, request: TranslationRequest) -> list[dict]:
    if not request.chapter_ids:
        return []
    chapters = {c.id: c for c in db.scalars(select(Chapter).where(Chapter.id.in_(request.chapter_ids)))}
    counts: dict[str, dict] = {}
    for chapter_id, translated, validated, status in db.execute(
        select(Segment.chapter_id, Segment.translation != "", Segment.validated, Segment.status).where(
            Segment.chapter_id.in_(list(chapters))
        )
    ):
        entry = counts.setdefault(chapter_id, {"segments": 0, "translated": 0, "validated": 0, "flagged": 0})
        entry["segments"] += 1
        entry["translated"] += bool(translated)
        entry["validated"] += bool(validated)
        entry["flagged"] += status in FLAGGED and not validated
    rows = []
    for chapter in sorted(chapters.values(), key=lambda c: c.position):
        entry = counts.get(chapter.id, {"segments": 0, "translated": 0, "validated": 0, "flagged": 0})
        rows.append(
            {
                "chapter_id": chapter.id,
                "external_id": chapter.external_id,
                "number": chapter.chapter_number,
                "title": chapter.title,
                **entry,
                "complete": bool(entry["segments"]) and entry["translated"] == entry["segments"],
            }
        )
    return rows


def message_for(text: str, language: str) -> str:
    return (english(text) or text) if language == "en" and text else text


def artifact_view(request: TranslationRequest) -> dict | None:
    artifact = request.artifact
    if not artifact:
        return None
    return {key: artifact.get(key) for key in ("format", "media_type", "filename", "size", "sha256", "created_at")}


def detail_view(db, request: TranslationRequest, language: str) -> dict:
    job = db.get(Job, request.job_id) if request.job_id else None
    project = db.get(Project, request.project_id) if request.project_id else None
    chapters = chapter_rows(db, request)
    total = sum(item["segments"] for item in chapters)
    translated = sum(item["translated"] for item in chapters)
    progress = project_progress(db, project) if project else None
    return {
        **summary(db, request),
        "created_at": request.created_at,
        "updated_at": request.updated_at,
        "finished_at": request.finished_at,
        "stage": progress["active_stage"] if progress and job else None,
        "step": (job.checkpoint or {}).get("step") if job else None,
        "progress": {
            "segments": total,
            "translated": translated,
            "percent": round(100 * translated / total) if total else 0,
            "stages": [
                {key: stage[key] for key in ("key", "done", "total", "percent")} for stage in progress["stages"]
            ]
            if progress
            else [],
        },
        "estimate": progress["estimate"] if progress and job else None,
        "error": message_for(request.error or (job.error if job else ""), language),
        "stop_reason": job.stop_reason if job else "",
        "next_attempt": job.next_attempt if job else 0,
        "chapters": {**(request.options.get("chapters") or {}), "items": chapters},
        "options": {
            key: request.options.get(key) for key in ("start", "final_review", "output_format")
        },
        "result": artifact_view(request),
        "report": request.report,
        "webhook": {
            "state": request.webhook_state or "pending",
            "attempts": request.webhook_attempts,
            "error": request.webhook_error,
        }
        if request.callback_url
        else None,
    }


def poll(request_id: str, owner_id: str) -> str | None:
    """The request's status after settling it; None when it is not the caller's."""
    with SessionLocal() as db:
        found = db.get(TranslationRequest, request_id)
        if found is None or found.owner_id != owner_id:
            return None
        refresh(db, request_id)
        found = db.get(TranslationRequest, request_id)
        job = db.get(Job, found.job_id) if found.job_id else None
        return public_status(found, job)


async def wait_for_end(db, request_id: str, caller: Caller, seconds: int) -> None:
    """Long poll, bounded by API_RESULT_MAX_WAIT_SECONDS; never holds a transaction while waiting."""
    await run_in_threadpool(db.rollback)
    deadline = time.monotonic() + min(seconds, settings().api_result_max_wait_seconds)
    while True:
        status = await run_in_threadpool(poll, request_id, caller.user.id)
        remaining = deadline - time.monotonic()
        if status is None or status in ENDED or remaining <= 0:
            return
        await asyncio.sleep(min(POLL_SECONDS, remaining))


def request_status(db, request_id: str, caller: Caller, language: str) -> dict:
    found = owned_request(db, request_id, caller)
    refresh(db, found.id)
    return detail_view(db, found, language)


@router.get("/translation-requests/{request_id}")
async def translation_request(
    request_id: str,
    http: Request,
    caller: Annotated[Caller, Depends(require("jobs:read"))],
    db: DB,
    wait: int = Query(default=0, ge=0, le=API_RESULT_WAIT_CEILING),
):
    if wait:
        await wait_for_end(db, request_id, caller, wait)
    language = preferred_language(http.headers.get("accept-language"))
    return await run_in_threadpool(request_status, db, request_id, caller, language)


@router.post("/translation-requests/{request_id}/{action}")
def control_request(
    request_id: str,
    action: Literal["pause", "resume", "cancel"],
    http: Request,
    caller: Annotated[Caller, Depends(require("jobs:control"))],
    db: DB,
):
    found = db.scalar(select(TranslationRequest).where(TranslationRequest.id == request_id).with_for_update())
    found = owned_request(db, found.id if found else "", caller)
    job = db.scalar(select(Job).where(Job.id == found.job_id).with_for_update()) if found.job_id else None
    if job is None:
        if action != "cancel" or found.status != "queued":
            raise HTTPException(
                409, {"code": "not_started",
                      "message": "Cette requête n’a pas de travail en cours : rien à mettre en pause ou à reprendre."},
            )  # fmt: skip
        finalize(found, "cancelled", "Requête annulée.")
    else:
        project = db.get(Project, job.project_id)
        control_job(db, project, job, action)
        if found.status not in ENDED:
            found.status = "running"
            if action == "cancel":
                settle(db, found)
    found.updated_at = time.time()
    db.commit()
    return detail_view(db, found, preferred_language(http.headers.get("accept-language")))


def negotiate(requested: str | None, accept: str, request: TranslationRequest) -> str:
    """`?format=` first, then the Accept header, then the request's own format (EPUB for an EPUB)."""
    if requested:
        return requested
    for part in accept.split(","):
        media = part.split(";")[0].strip().casefold()
        if media in ACCEPTED:
            return ACCEPTED[media]
    return default_format(request)


def not_ready(status: str, incomplete: list[str], error: str) -> HTTPException:
    if status in {"failed", "cancelled"}:
        return HTTPException(
            409, {"code": "request_failed" if status == "failed" else "request_cancelled",
                  "message": "Cette requête n’a pas abouti : aucun résultat complet. Demandez un résultat partiel "
                             "(partial=true) pour obtenir ce qui a été traduit.",
                  "status": status, "reason": error, "incomplete_chapters": incomplete},
        )  # fmt: skip
    return HTTPException(
        409,
        {
            "code": "result_not_ready",
            "message": "La traduction de cette requête n’est pas terminée : réessayez plus tard, "
            "ou demandez un résultat partiel (partial=true).",
            "status": status,
            "incomplete_chapters": incomplete,
        },
        headers={"Retry-After": "5"},
    )


def result_response(db, request_id: str, caller: Caller, requested: str | None, partial: bool, accept: str):
    found = owned_request(db, request_id, caller)
    refresh(db, found.id)
    project = db.get(Project, found.project_id) if found.project_id else None
    if project is None:
        raise HTTPException(404, {"code": "volume_not_found", "message": "Le volume de cette requête a été supprimé."})
    job = db.get(Job, found.job_id) if found.job_id else None
    fmt = negotiate(requested, accept, found)
    if fmt == "epub" and found.options.get("input") != "epub":
        raise HTTPException(
            409, {"code": "format_unavailable",
                  "message": "Le format EPUB n’est disponible que pour un EPUB envoyé."},
        )  # fmt: skip
    status = public_status(found, job)
    texts = request_texts(db, found, project)
    incomplete = [item.external_id or item.chapter_id for item in texts if not item.complete]
    wanted = found.chapter_ids or []
    if status in SUCCESS:
        complete = status == "completed"
    else:
        # A request without a job ("imported") is complete when its chapters are all translated.
        complete = status == "imported" and bool(wanted) and not incomplete and len(texts) == len(wanted)
        if not complete and not partial:
            raise not_ready(status, incomplete, found.error)
    content = stored(found) if (found.artifact or {}).get("format") == fmt else None
    if content is not None:
        filename = found.artifact.get("filename") or f"result.{fmt}"
    else:
        try:
            rendered = render(db, found, project, job, fmt, status, found.report)
        except DeliveryFailed as exc:
            raise HTTPException(
                422, {"code": "delivery_failed", "message": exc.reason, "errors": exc.details}
            ) from None
        content, filename = rendered.content, rendered.filename
    headers = {
        "X-Libris-Complete": "true" if complete else "false",
        "X-Libris-Status": status,
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
    }
    if fmt == "json":
        headers.pop("Content-Disposition")
    return Response(content, media_type=MEDIA_TYPES[fmt], headers=headers)


@router.get("/translation-requests/{request_id}/result")
async def translation_result(
    request_id: str,
    http: Request,
    caller: Annotated[Caller, Depends(require("results:read"))],
    db: DB,
    format: Literal[FORMATS] | None = None,  # noqa: A002 - the documented query parameter
    partial: bool = Query(default=False),
    wait: int = Query(default=0, ge=0, le=API_RESULT_WAIT_CEILING),
):
    if wait:
        await wait_for_end(db, request_id, caller, wait)
    accept = http.headers.get("accept", "")
    return await run_in_threadpool(result_response, db, request_id, caller, format, partial, accept)


def series_summary(db, series: Series) -> dict:
    volumes = db.scalar(select(func.count()).select_from(Project).where(Project.series_id == series.id))
    return {
        "id": series.id,
        "name": series.name,
        "kind": series.kind,
        "source_language": series.source_language,
        "target_language": series.target_language,
        "archived": series.archived_at is not None,
        "volumes": volumes,
        "created_at": series.created_at,
        "updated_at": series.updated_at,
    }


@router.get("/providers")
def list_providers(caller: Annotated[Caller, Depends(require("content:write"))], db: DB):
    """Providers a request may name in `provider_id`: identity and model only, never an address or a key.

    `default_for_series` lists the caller's series that use the provider by default."""
    defaults: dict[str, list[str]] = {}
    for series_id, provider_id in db.execute(
        select(Series.id, Series.provider_id)
        .where(Series.owner_id == caller.user.id, Series.provider_id.is_not(None))
        .order_by(Series.name)
    ):
        defaults.setdefault(provider_id, []).append(series_id)
    return [
        {**{key: getattr(provider, key) for key in SHARED_FIELDS}, "default_for_series": defaults.get(provider.id, [])}
        for provider in db.scalars(select(Provider).order_by(Provider.name))
    ]


@router.get("/series")
def list_series(caller: Annotated[Caller, Depends(require("series:read"))], db: DB):
    series = db.scalars(select(Series).where(Series.owner_id == caller.user.id).order_by(Series.name))
    return [series_summary(db, item) for item in series]


@router.get("/series/{series_id}")
def get_series(series_id: str, caller: Annotated[Caller, Depends(require("series:read"))], db: DB):
    series = db.get(Series, series_id)
    if not series or series.owner_id != caller.user.id:
        raise HTTPException(404, {"code": "series_not_found", "message": "Série introuvable."})
    projects = db.scalars(
        select(Project).where(Project.series_id == series.id).order_by(Project.volume_number, Project.created_at)
    )
    counts = dict(
        db.execute(
            select(Chapter.project_id, func.count())
            .join(Project, Project.id == Chapter.project_id)
            .where(Project.series_id == series.id)
            .group_by(Chapter.project_id)
        ).all()
    )
    return {
        **series_summary(db, series),
        "volume_list": [
            {
                "project_id": project.id,
                "title": project.title,
                "volume_number": project.volume_number,
                "external_id": project.external_id,
                "source_format": project.source_format,
                "project_kind": project.project_kind,
                "status": project.status,
                "chapters": counts.get(project.id, 0),
            }
            for project in projects
        ],
    }

