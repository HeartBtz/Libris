"""Automation API (`/api/v1`): JSON translation requests authenticated by API tokens.

Separate from the interface's API: no session cookie, versioned paths, errors shaped as
`{"detail": {"code", "message", ...}}`. A request is committed to SQL before the 202 answer; the
pipeline runs in the worker and the client polls the status, then fetches the result. There is no
webhook: nothing is ever sent from Libris to an address named by a client.
"""

import hashlib
import time
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.api.projects import control_job
from app.api.tokens import Caller, require
from app.config import settings
from app.engines.exports.text import chapters_zip, consolidated, safe_filename, volume_texts
from app.engines.ingestion.payload import (
    SCHEMA_VERSION,
    PayloadRejected,
    TranslationPayload,
    parse_payload,
    payload_asset,
)
from app.engines.ingestion.store import Files, attach, find_series, get_or_create_series, lock, store_asset
from app.i18n import english, preferred_language
from app.jobs.requests import advance, check_conflicts, public_status, volume_lock
from app.models import (
    Chapter,
    Issue,
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


def target_series(db, owner_id: str, payload: TranslationPayload) -> Series:
    reference = payload.series
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
                source_language=payload.source_language, target_language=payload.target_language,
                provider_id=payload.pipeline.provider_id, quality=payload.pipeline.quality,
                context_backend=payload.pipeline.context_backend,
            )  # fmt: skip
    if series.archived_at:
        raise HTTPException(
            409, {"code": "series_archived",
                  "message": "Cette série est archivée : restaurez-la avant d’y ajouter du contenu."},
        )  # fmt: skip
    return series


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


def create_request(db, caller: Caller, payload: TranslationPayload, key: str | None) -> tuple[TranslationRequest, bool]:
    owner_id = caller.user.id
    data = payload_asset(payload)
    digest = hashlib.sha256(data.data).hexdigest()
    for name in (f"key:{key}" if key else "", f"external:{payload.external_id}" if payload.external_id else ""):
        if name:
            lock(db, f"api-request:{owner_id}:{name}")
    found = existing(db, owner_id, key, payload.external_id)
    if found:
        return replayed(found, digest), True
    if payload.pipeline.start and "pipeline:start" not in caller.token.scopes:
        raise HTTPException(
            403, {"code": "insufficient_scope", "scope": "pipeline:start",
                  "message": "Ce jeton n’a pas la permission « pipeline:start »."},
        )  # fmt: skip
    if payload.pipeline.provider_id and not db.get(Provider, payload.pipeline.provider_id):
        raise HTTPException(422, {"code": "unknown_provider", "message": "Provider inconnu."})
    files = Files()
    try:
        series = target_series(db, owner_id, payload)
        project = target_volume(db, owner_id, series, payload)
        if payload.pipeline.start and not (payload.pipeline.provider_id or project.provider_id):
            raise HTTPException(
                422, {"code": "provider_required",
                      "message": "Aucun provider pour ce volume : indiquez pipeline.provider_id."},
            )  # fmt: skip
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
            options={
                "asset_id": asset.id,
                "start": payload.pipeline.start,
                "final_review": payload.pipeline.final_review,
                "output_format": payload.output.format,
                "ingested": False,
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


def submit(db, caller: Caller, payload: TranslationPayload, key: str | None) -> tuple[dict, bool]:
    request, again = create_request(db, caller, payload, key)
    return summary(db, request), again


async def read_payload(request: Request) -> TranslationPayload:
    kind = request.headers.get("content-type", "").split(";")[0].strip().casefold()
    limit = settings().api_payload_mb * 1024**2
    if kind == "application/json":
        data = await request.body()
    elif kind == "multipart/form-data":
        form = await request.form(max_files=1, max_fields=0)
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or len(form) != 1:
            raise HTTPException(
                422, {"code": "invalid_payload", "message": "Envoyez un seul fichier JSON dans le champ « file »."}
            )
        if not (upload.filename or "").casefold().endswith(".json"):
            raise HTTPException(
                422, {"code": "invalid_payload", "message": "Le fichier envoyé doit être un fichier .json."}
            )
        data = await upload.read(limit + 1)
    else:
        raise HTTPException(
            415, {"code": "unsupported_media_type",
                  "message": "Envoyez application/json, ou un fichier .json en multipart/form-data."},
        )  # fmt: skip
    if len(data) > limit:
        raise HTTPException(
            413, {"code": "payload_too_large",
                  "message": f"Requête trop volumineuse : {settings().api_payload_mb} Mo au maximum."},
        )  # fmt: skip
    try:
        return parse_payload(data, settings().api_max_chapters)
    except PayloadRejected as exc:
        raise HTTPException(
            422, {"code": "invalid_payload", "message": str(exc), "errors": exc.errors}
        ) from None


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
    payload = await read_payload(request)
    body, again = await run_in_threadpool(submit, db, caller, payload, key)
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
    }


@router.get("/translation-requests/{request_id}")
def translation_request(
    request_id: str, http: Request, caller: Annotated[Caller, Depends(require("jobs:read"))], db: DB
):
    found = owned_request(db, request_id, caller)
    return detail_view(db, found, preferred_language(http.headers.get("accept-language")))


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
        found.status = "cancelled"
    else:
        project = db.get(Project, job.project_id)
        control_job(db, project, job, action)
        found.status = "cancelled" if action == "cancel" else "running"
    found.updated_at = time.time()
    db.commit()
    return detail_view(db, found, preferred_language(http.headers.get("accept-language")))


def unresolved(db, chapter_ids: list[str]) -> dict[str, dict]:
    """Per chapter: unresolved quality issues and passages still flagged for a person."""
    found: dict[str, dict] = {chapter_id: {"issues": [], "flagged_passages": []} for chapter_id in chapter_ids}
    if not chapter_ids:
        return found
    for issue, chapter_id in db.execute(
        select(Issue, Segment.chapter_id)
        .join(Segment, Segment.id == Issue.segment_id)
        .where(Segment.chapter_id.in_(chapter_ids), Issue.resolved.is_(False))
        .order_by(Segment.position)
    ):
        found[chapter_id]["issues"].append(
            {"segment_id": issue.segment_id, "severity": issue.severity, "code": issue.code, "message": issue.message}
        )
    for segment_id, chapter_id, position, status in db.execute(
        select(Segment.id, Segment.chapter_id, Segment.position, Segment.status)
        .where(Segment.chapter_id.in_(chapter_ids), Segment.status.in_(FLAGGED), Segment.validated.is_(False))
        .order_by(Segment.position)
    ):
        found[chapter_id]["flagged_passages"].append({"segment_id": segment_id, "position": position, "status": status})
    return found


def strategy(db, project: Project, job: Job | None, request: TranslationRequest) -> dict:
    provider = db.get(Provider, (job.provider_id if job else None) or project.provider_id or "")
    return {
        # Name and model only: never the address or the key of the provider.
        "provider": {"name": provider.name, "model": provider.model} if provider else None,
        "quality": project.quality,
        "context_backend": project.context_backend,
        "final_review": bool(settings().final_review_enabled and request.options.get("final_review", True)),
    }


@router.get("/translation-requests/{request_id}/result")
def translation_result(
    request_id: str,
    caller: Annotated[Caller, Depends(require("results:read"))],
    db: DB,
    format: Literal["json", "txt", "txt-zip"] | None = None,  # noqa: A002 - the documented query parameter
    partial: bool = Query(default=False),
):
    found = owned_request(db, request_id, caller)
    project = db.get(Project, found.project_id) if found.project_id else None
    if project is None:
        raise HTTPException(404, {"code": "volume_not_found", "message": "Le volume de cette requête a été supprimé."})
    job = db.get(Job, found.job_id) if found.job_id else None
    wanted = set(found.chapter_ids)
    texts = [item for item in volume_texts(db, project) if item.chapter_id in wanted]
    incomplete = [item for item in texts if not item.complete]
    status = public_status(found, job)
    finished = status == "completed" or (status == "imported" and not incomplete)
    if (not finished or incomplete or len(texts) != len(wanted) or not wanted) and not partial:
        raise HTTPException(
            409,
            {
                "code": "result_not_ready",
                "message": "La traduction de cette requête n’est pas terminée : réessayez plus tard, "
                "ou demandez un résultat partiel (partial=true).",
                "status": status,
                "incomplete_chapters": [item.external_id or item.chapter_id for item in incomplete],
            },
        )
    output = format or found.options.get("output_format") or "json"
    complete = finished and not incomplete and len(texts) == len(wanted) and bool(wanted)
    headers = {"X-Libris-Complete": "true" if complete else "false"}
    name = safe_filename(project.title)
    if output == "txt":
        return Response(
            consolidated(project, texts).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={**headers, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name + '.txt')}"},
        )
    if output == "txt-zip":
        return Response(
            chapters_zip(project, texts),
            media_type="application/zip",
            headers={**headers, "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name + '.zip')}"},
        )
    problems = unresolved(db, [item.chapter_id for item in texts])
    series = db.get(Series, project.series_id) if project.series_id else None
    return JSONResponse(
        {
            "schema_version": SCHEMA_VERSION,
            "request_id": found.id,
            "external_id": found.external_id,
            "status": status,
            "complete": complete,
            "series": {"id": series.id, "name": series.name} if series else None,
            "volume": {
                "project_id": project.id,
                "external_id": project.external_id,
                "number": project.volume_number,
                "title": project.title,
            },
            "source_language": project.source_language,
            "target_language": project.target_language,
            "strategy": strategy(db, project, job, found),
            "incomplete_chapters": [item.external_id or item.chapter_id for item in incomplete],
            "chapters": [
                {
                    "chapter_id": item.chapter_id,
                    "external_id": item.external_id,
                    "number": item.number,
                    "title": item.title,
                    "translated_title": item.translated_title,
                    "complete": item.complete,
                    "missing_segments": item.missing_segments,
                    "translation": item.text,
                    "source_sha256": item.source_checksum,
                    "sha256": item.checksum,
                    "review": {"segments": item.segments, "validated": item.validated, "flagged": item.flagged},
                    **problems[item.chapter_id],
                }
                for item in texts
            ],
        },
        headers=headers,
    )


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

