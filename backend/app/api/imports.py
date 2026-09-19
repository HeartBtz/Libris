"""Two-phase imports: upload and inspect files, let the person correct the proposal, then commit.

Nothing becomes a series, a volume or a chapter before the commit. Uploaded files wait under
DATA_DIR/staging (never under a name taken from the upload) until the commit or their expiry.
Repeating a commit answers what the first one did; a file already in the library is reported, not
imported twice.
"""

import asyncio
import hashlib
import logging
import shutil
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import Field, model_validator
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.api.projects import import_book, project_views
from app.config import settings
from app.engines.epub.check import epubcheck
from app.engines.ingestion import UPLOAD_EXTENSIONS, EpubAdapter, TextRejected, chapter_adapter
from app.engines.ingestion.naming import (
    LOW,
    MEDIUM,
    missing_numbers,
    natural_key,
    normalize_series,
    propose_chapters,
    propose_volumes,
    series_from_names,
)
from app.engines.ingestion.passages import passage_chars
from app.engines.ingestion.store import (
    Files,
    add_chapters,
    attach,
    data_path,
    epub_duplicate,
    find_series,
    get_or_create_series,
    safe_display_name,
    serial_container,
    text_resource,
)
from app.engines.series.bible import refresh_series
from app.i18n import localize, preferred_language
from app.jobs.fairness import QueueRefused, admit
from app.jobs.launch import launch
from app.jobs.queue import emit
from app.models import Chapter, ImportSession, Project, Provider, Series, SourceAsset
from app.models.common import uid
from app.schemas import StrictModel
from app.security import DB, CurrentUser

router = APIRouter(prefix="/api/imports")
logger = logging.getLogger("epub.imports")
staging_locks: dict[str, asyncio.Lock] = {}
LOCALIZED = {"warnings", "errors", "reason", "number_reason", "message", "series_reason"}


def localized(request: Request, value):
    """Inspection texts are written in French like the errors; an English interface gets English."""
    language = preferred_language(request.headers.get("accept-language"))
    if language != "en":
        return value
    if isinstance(value, list):
        return [localized(request, item) for item in value]
    if isinstance(value, dict):
        return {
            key: (localize(item, language) if key in LOCALIZED else localized(request, item))
            for key, item in value.items()
        }
    return value


def staging_dir(session: ImportSession):
    return data_path(f"staging/{session.id}")


def owned_session(db, session_id: str, user, lock: bool = False) -> ImportSession:
    query = select(ImportSession).where(ImportSession.id == session_id)
    if lock:
        # Re-read the row: an earlier read in this request must not hide another request's upload.
        query = query.with_for_update().execution_options(populate_existing=True)
    session = db.scalar(query)
    if not session or session.owner_id != user.id:
        raise HTTPException(404, "Import introuvable ou expiré.")
    if session.expires_at < time.time() and not session.result:
        raise HTTPException(410, "Cet import a expiré : ajoutez de nouveau les fichiers.")
    return session


class SessionInput(StrictModel):
    # One EPUB is a volume; one TXT, Markdown, HTML or DOCX file is a chapter.
    format: Literal["epub", "txt", "md", "html", "docx"]


@router.post("", status_code=201)
def create_session(body: SessionInput, user: CurrentUser, db: DB):
    session = ImportSession(
        id=uid(),
        owner_id=user.id,
        format=body.format,
        files=[],
        expires_at=time.time() + settings().import_session_hours * 3600,
    )
    db.add(session)
    db.commit()
    staging_dir(session).mkdir(parents=True, exist_ok=True)
    return session_view(db, session)


def inspect_file(fmt: str, name: str, data: bytes) -> dict:
    adapter = EpubAdapter() if fmt == "epub" else chapter_adapter(fmt, settings().text_chapter_max_chars)
    found = adapter.inspect(name, data)
    return {
        "format": found.format,
        "title": found.title,
        "author": found.author,
        "language": found.language,
        "series": found.series,
        "series_index": found.series_index,
        "chapter_number": found.chapter_number,
        "number_confidence": found.number_confidence,
        "number_reason": found.number_reason,
        "warnings": found.warnings,
        "errors": found.errors,
        "meta": found.meta,
    }


@router.post("/{session_id}/files", status_code=201)
async def add_file(session_id: str, file: UploadFile, request: Request, user: CurrentUser, db: DB):
    limits = settings()
    session = owned_session(db, session_id, user)
    if session.result:
        raise HTTPException(409, "Cet import est déjà confirmé.")
    data = await file.read(limits.max_upload_mb * 1024**2 + 1)
    if len(data) > limits.max_upload_mb * 1024**2:
        raise HTTPException(413, f"Requête trop volumineuse : {limits.max_upload_mb} Mo au maximum.")
    name = safe_display_name(file.filename or "fichier")
    extension = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    if extension not in UPLOAD_EXTENSIONS[session.format]:
        raise HTTPException(422, f"Ce fichier n’est pas un {session.format.upper()} : « {name} ».")
    inspection = await run_in_threadpool(inspect_file, session.format, name, data)
    sha256 = hashlib.sha256(data).hexdigest()
    # Browsers upload several files at once: the list is rewritten under a lock, or an upload is lost
    # (SQLite has no row lock; PostgreSQL also locks the row below).
    async with staging_locks.setdefault(session_id, asyncio.Lock()):
        return await run_in_threadpool(
            record_file, db, session_id, user, name, data, sha256, inspection, request
        )


def record_file(db, session_id, user, name, data, sha256, inspection, request) -> dict:
    limits = settings()
    session = owned_session(db, session_id, user, lock=True)
    files = list(session.files)
    if len(files) >= limits.import_max_files:
        raise HTTPException(422, f"Un import accepte au plus {limits.import_max_files} fichiers.")
    if sum(item["size"] for item in files) + len(data) > limits.import_max_session_mb * 1024**2:
        raise HTTPException(413, f"Import trop volumineux : {limits.import_max_session_mb} Mo au maximum.")
    index = max((item["index"] for item in files), default=-1) + 1
    duplicate = None
    earlier = next((item for item in files if item["sha256"] == sha256), None)
    if earlier:
        duplicate = {"kind": "batch", "index": earlier["index"], "name": earlier["name"]}
    elif session.format == "epub":
        existing = epub_duplicate(db, user.id, sha256)
        if existing:
            duplicate = {"kind": "library", "project_id": existing.id, "title": existing.title}
    else:
        existing = db.scalar(
            select(Project)
            .join(SourceAsset, SourceAsset.project_id == Project.id)
            .where(Project.owner_id == user.id, SourceAsset.sha256 == sha256, SourceAsset.format == session.format)
            .limit(1)
        )
        if existing:
            duplicate = {"kind": "library", "project_id": existing.id, "title": existing.title}
    path = staging_dir(session) / f"{index}.bin"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    entry = {"index": index, "name": name, "size": len(data), "sha256": sha256, "duplicate": duplicate, **inspection}
    session.files = [*files, entry]
    db.commit()
    return localized(request, entry)


@router.delete("/{session_id}/files/{index}")
def remove_file(session_id: str, index: int, user: CurrentUser, db: DB):
    session = owned_session(db, session_id, user, lock=True)
    if session.result:
        raise HTTPException(409, "Cet import est déjà confirmé.")
    session.files = [item for item in session.files if item["index"] != index]
    db.commit()
    (staging_dir(session) / f"{index}.bin").unlink(missing_ok=True)
    return {"ok": True}


def proposal(db, session: ImportSession, user, series_id: str | None, project_id: str | None) -> dict:
    usable = [item for item in session.files if not item["errors"]]
    names = [item["name"] for item in usable]
    if session.format == "epub":
        guesses = propose_volumes(names, [{"series_index": item["series_index"]} for item in usable])
        from_names = series_from_names(names)
        declared = {normalize_series(item["series"]): item["series"] for item in usable if item["series"]}
        if from_names.name:
            name, confidence, reason = from_names.name, from_names.confidence, from_names.reason
        elif len(declared) == 1:
            name, confidence, reason = next(iter(declared.values())), MEDIUM, "métadonnées de série de l’EPUB"
        else:
            name, confidence, reason = "", LOW, "aucun nom commun dans les noms de fichiers"
        existing_series = find_series(db, user.id, name) if name else None
        target = db.get(Series, series_id) if series_id else existing_series
        taken = {}
        if target and target.owner_id == user.id:
            for project in db.scalars(select(Project).where(Project.series_id == target.id)):
                if project.volume_number is not None:
                    taken[project.volume_number] = {"project_id": project.id, "title": project.title}
        items = []
        for item, guess in zip(usable, guesses, strict=True):
            items.append(
                {
                    "index": item["index"],
                    "title": item["title"],
                    "volume_number": guess.number,
                    "confidence": guess.confidence,
                    "reason": guess.reason,
                    "warnings": guess.warnings,
                    "existing_volume": taken.get(guess.number) if guess.number is not None else None,
                }
            )
        numbers = [item["volume_number"] for item in items if item["volume_number"] is not None]
        return {
            "series": {
                "name": name,
                "confidence": confidence,
                "series_reason": reason,
                "existing_series_id": existing_series.id if existing_series else None,
            },
            "items": sorted(items, key=lambda item: (item["volume_number"] is None, item["volume_number"] or 0)),
            "duplicate_numbers": sorted({n for n in numbers if numbers.count(n) > 1}),
            "missing_numbers": missing_numbers([*numbers, *taken]),
        }
    guesses = propose_chapters(names)
    target = db.get(Project, project_id) if project_id else None
    existing: dict[float, Chapter] = {}
    if target and target.owner_id == user.id:
        for chapter in db.scalars(select(Chapter).where(Chapter.project_id == target.id)):
            if chapter.chapter_number is not None:
                existing[chapter.chapter_number] = chapter
    items = []
    for item, guess in zip(usable, guesses, strict=True):
        current = existing.get(guess.value) if guess.value is not None else None
        items.append(
            {
                "index": item["index"],
                "title": item["title"],
                "chapter_number": guess.value,
                "confidence": guess.confidence,
                "reason": guess.reason or "aucun numéro trouvé",
                "existing_chapter": {
                    "chapter_id": current.id,
                    "title": current.title,
                    "same_content": current.source_checksum == item["meta"].get("checksum"),
                }
                if current
                else None,
            }
        )
    items.sort(key=lambda item: (item["chapter_number"] is None, item["chapter_number"] or 0, natural_key(item["title"])))
    numbers = [int(item["chapter_number"]) for item in items if item["chapter_number"] is not None and float(item["chapter_number"]).is_integer()]
    counted = [item["chapter_number"] for item in items if item["chapter_number"] is not None]
    return {
        "items": items,
        "duplicate_numbers": sorted({n for n in counted if counted.count(n) > 1}),
        "missing_numbers": missing_numbers([*numbers, *(int(n) for n in existing if float(n).is_integer())]),
    }


def session_view(db, session: ImportSession, user=None, series_id=None, project_id=None) -> dict:
    view = {
        "id": session.id,
        "format": session.format,
        "files": session.files,
        "expires_at": session.expires_at,
        "result": session.result,
        # Whether low-confidence numbers must be confirmed (IMPORT_CONFIRM_LOW_CONFIDENCE): the interface
        # only asks a person when the server would refuse the guess.
        "confirm_low_confidence": settings().import_confirm_low_confidence,
    }
    if user is not None:
        view["proposal"] = proposal(db, session, user, series_id, project_id)
    return view


@router.get("/{session_id}")
def get_session(
    session_id: str, request: Request, user: CurrentUser, db: DB, series_id: str | None = None,
    project_id: str | None = None,
):
    session = owned_session(db, session_id, user)
    return localized(request, session_view(db, session, user, series_id, project_id))


class Destination(StrictModel):
    mode: Literal["series", "standalone"]
    series_id: str | None = None
    series_name: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def named(self):
        if self.mode == "series" and not self.series_id and not self.series_name.strip():
            raise ValueError("Choisissez une série existante ou nommez la nouvelle série.")
        return self


class Target(StrictModel):
    mode: Literal["serial", "volume", "new_volume"] = "serial"
    project_id: str | None = None
    volume_number: int | None = Field(default=None, ge=1, le=10000)
    volume_title: str = Field(default="", max_length=500)


class Item(StrictModel):
    index: int
    title: str = Field(default="", max_length=500)
    volume_number: int | None = Field(default=None, ge=1, le=10000)
    chapter_number: float | None = Field(default=None, ge=0, le=100000)
    confirmed: bool = False
    replace: bool = False
    skip: bool = False


class Defaults(StrictModel):
    source_language: str | None = Field(default=None, min_length=2, max_length=80)
    target_language: str | None = Field(default=None, min_length=2, max_length=80)
    provider_id: str | None = None
    quality: Literal["fast", "normal", "high", "maximum"] | None = None
    context_backend: Literal["internal", "openviking", "hybrid"] | None = None
    # Passage size of what this import creates (empty: the volume's choice, else PASSAGE_MAX_CHARS).
    passage_max_chars: int | None = Field(default=None, ge=500, le=20000)


class CommitInput(StrictModel):
    destination: Destination
    target: Target | None = None
    items: list[Item] = Field(min_length=1, max_length=5000)
    first_line_title: bool = False
    discard_human: bool = False
    settings: Defaults = Field(default_factory=Defaults)
    # The whole pipeline unless the caller chooses otherwise: nothing waits for a person by default.
    start: Literal["none", "analyze", "pipeline"] = "pipeline"


class Rejected(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors


def decide(decisions: list[dict], entry: dict, field: str, value, reason: str) -> None:
    """An automatic choice made instead of asking the person: recorded in the result and logged."""
    decisions.append({"index": entry["index"], "name": entry["name"], field: value, "reason": reason})
    logger.info("import=auto_decision file=%s %s=%s", entry["index"], field, value)


def check_epub_plan(
    session: ImportSession, body: CommitInput, guesses: dict[int, dict], taken: set[int] | None = None,
    decisions: list[dict] | None = None,
) -> list[Item]:  # fmt: skip
    errors = []
    decisions = [] if decisions is None else decisions
    confirm = settings().import_confirm_low_confidence
    files = {item["index"]: item for item in session.files}
    chosen = []
    used = set(taken or ()) | {item.volume_number for item in body.items if item.volume_number and not item.skip}
    for item in body.items:
        entry = files.get(item.index)
        if entry is None:
            errors.append(f"Fichier n° {item.index} inconnu dans cet import.")
            continue
        if item.skip:
            continue
        if entry["errors"]:
            errors.append(f"« {entry['name']} » est illisible : retirez-le de l’import.")
        if entry["duplicate"]:
            errors.append(f"« {entry['name']} » est déjà dans votre bibliothèque ou dans ce lot : retirez-le.")
        if body.destination.mode == "series":
            guess = guesses.get(item.index, {})
            if item.volume_number is None and confirm:
                errors.append(f"Indiquez le numéro de volume de « {entry['name']} ».")
            elif item.volume_number is None:
                number = max(used, default=0) + 1
                used.add(number)
                item = item.model_copy(update={"volume_number": number})
                decide(decisions, entry, "volume_number", number, "aucun numéro : volume suivant de la série")
            elif guess.get("confidence") == LOW and item.volume_number == guess.get("volume_number") and not item.confirmed:
                if confirm:
                    errors.append(f"Confirmez le numéro de volume de « {entry['name']} » : il n’a pas pu être déduit avec certitude.")
                else:
                    decide(decisions, entry, "volume_number", item.volume_number,
                           f"numéro peu sûr accepté : {guess.get('reason') or 'meilleure proposition'}")  # fmt: skip
        chosen.append(item)
    if not chosen:
        errors.append("Aucun fichier à importer.")
    numbers = [item.volume_number for item in chosen if item.volume_number is not None]
    for number in sorted({n for n in numbers if numbers.count(n) > 1}):
        errors.append(f"Le volume {number} apparaît plusieurs fois dans ce lot.")
    if errors:
        raise Rejected(errors)
    return sorted(chosen, key=lambda item: (item.volume_number or 0, item.index))


def apply_defaults(project: Project, defaults: Defaults) -> None:
    for key in ("source_language", "target_language", "provider_id", "quality", "context_backend"):
        value = getattr(defaults, key)
        if value is not None:
            setattr(project, key, value)
    if defaults.passage_max_chars:
        # Later chapters of a volume created here are cut like its first ones.
        project.config = {**(project.config or {}), "passage_max_chars": defaults.passage_max_chars}


def commit_epub(db, session: ImportSession, body: CommitInput, user, files: Files) -> dict:
    guesses = {item["index"]: item for item in proposal(db, session, user, body.destination.series_id, None)["items"]}
    target = db.get(Series, body.destination.series_id) if body.destination.series_id else (
        find_series(db, user.id, body.destination.series_name) if body.destination.series_name.strip() else None
    )
    numbers = set()
    if target is not None and target.owner_id == user.id:
        numbers = {n for n in db.scalars(select(Project.volume_number).where(Project.series_id == target.id)) if n}
    decisions: list[dict] = []
    chosen = check_epub_plan(session, body, guesses, numbers, decisions)
    series = None
    if body.destination.mode == "series":
        series = resolve_series(db, body.destination, user, "books", body.settings)
        taken = {
            project.volume_number: project.title
            for project in db.scalars(select(Project).where(Project.series_id == series.id))
            if project.volume_number is not None
        }
        clashes = [
            f"Le volume {item.volume_number} existe déjà dans « {series.name} » : « {taken[item.volume_number]} »."
            for item in chosen
            if item.volume_number in taken
        ]
        if clashes:
            raise Rejected(clashes)
    names = {item["index"]: item["name"] for item in session.files}
    created = []
    for item in chosen:
        data = (staging_dir(session) / f"{item.index}.bin").read_bytes()
        project = import_book(
            db,
            user.id,
            data,
            name=names[item.index],
            series=series,
            volume_number=item.volume_number if series else None,
            files=files,
            passage_max_chars=body.settings.passage_max_chars,
        )
        if item.title.strip():
            project.title = item.title.strip()[:500]
        apply_defaults(project, body.settings)
        if len(chosen) <= 3:
            try:
                validation = epubcheck(data)
            except ValueError as exc:
                validation = {"available": True, "valid": None, "message": str(exc)}
            project.book_info = dict(project.book_info, validation=validation)
        emit(db, project.id, status="imported", step="parsing")
        created.append(project)
    if series:
        refresh_series(db, series.id)
    return {
        "series_id": series.id if series else None,
        "series_name": series.name if series else "",
        "projects": [
            {"id": p.id, "title": p.title, "volume_number": p.volume_number, "status": "created"} for p in created
        ],
        "chapters": {"created": 0, "unchanged": 0, "replaced": 0, "items": []},
        "decisions": decisions,
    }


def resolve_series(db, destination: Destination, user, kind: str, defaults: Defaults) -> Series:
    if destination.series_id:
        series = db.get(Series, destination.series_id)
        if not series or series.owner_id != user.id:
            raise HTTPException(404, "Série introuvable.")
        if series.archived_at:
            raise HTTPException(409, "Cette série est archivée : restaurez-la avant d’y ajouter du contenu.")
        return series
    existing = find_series(db, user.id, destination.series_name)
    series = existing or get_or_create_series(db, user.id, destination.series_name, kind)
    if not existing:
        for key in ("source_language", "target_language", "provider_id", "quality", "context_backend"):
            value = getattr(defaults, key)
            if value is not None:
                setattr(series, key, value)
    return series


def text_volume(db, series: Series, target: Target, defaults: Defaults, user) -> tuple[Project, bool]:
    if target.mode == "serial":
        existed = db.scalar(select(Project.id).where(Project.series_id == series.id, Project.project_kind == "serial"))
        project = serial_container(db, series)
        if not existed:
            apply_defaults(project, defaults)
        return project, not existed
    if target.mode == "volume":
        project = db.get(Project, target.project_id or "")
        if not project or project.owner_id != user.id or project.series_id != series.id:
            raise HTTPException(404, "Volume introuvable dans cette série.")
        if project.archived_at:
            raise HTTPException(409, "Ce volume est archivé : restaurez-le avant d’y ajouter des chapitres.")
        return project, False
    if target.volume_number is None:
        raise Rejected(["Indiquez le numéro du nouveau volume."])
    clash = db.scalar(
        select(Project).where(Project.series_id == series.id, Project.volume_number == target.volume_number)
    )
    if clash:
        raise Rejected([f"Le volume {target.volume_number} existe déjà dans « {series.name} » : « {clash.title} »."])
    project = Project(
        owner_id=user.id,
        title=(target.volume_title.strip() or f"{series.name} — {target.volume_number}")[:500],
        author=(series.authors or [""])[0][:500],
        source_language=series.source_language or "en",
        volume_number=target.volume_number,
        source_format="txt",
        project_kind="volume",
        book_info={},
        import_meta={"version": 1, "adapter": "txt"},
        original_hash="",
        original_path="",
    )
    attach(project, series)
    for key in ("target_language", "provider_id", "quality", "context_backend"):
        if getattr(series, key):
            setattr(project, key, getattr(series, key))
    apply_defaults(project, defaults)
    db.add(project)
    db.flush()
    return project, True


def commit_txt(db, session: ImportSession, body: CommitInput, user, files: Files) -> dict:
    if body.destination.mode != "series":
        raise Rejected(["Des chapitres TXT appartiennent obligatoirement à une série : choisissez-la ou créez-la."])
    target = body.target or Target()
    entries = {item["index"]: item for item in session.files}
    guesses = {item["index"]: item for item in proposal(db, session, user, None, None)["items"]}
    errors, chosen, decisions = [], [], []
    confirm = settings().import_confirm_low_confidence
    for item in body.items:
        entry = entries.get(item.index)
        if entry is None:
            errors.append(f"Fichier n° {item.index} inconnu dans cet import.")
            continue
        if item.skip:
            continue
        if entry["errors"]:
            errors.append(f"« {entry['name']} » est illisible : retirez-le de l’import.")
        if entry["duplicate"] and entry["duplicate"]["kind"] == "batch":
            errors.append(f"« {entry['name']} » est en double dans ce lot : retirez-le.")
        guess = guesses.get(item.index, {})
        if item.chapter_number is None and not item.confirmed:
            if confirm:
                errors.append(f"Indiquez le numéro de chapitre de « {entry['name']} » ou confirmez qu’il n’en a pas.")
            else:
                decide(decisions, entry, "chapter_number", None, "aucun numéro : chapitre placé d’après son nom")
        elif guess.get("confidence") == LOW and item.chapter_number == guess.get("chapter_number") and not item.confirmed:
            if confirm:
                errors.append(f"Confirmez le numéro de chapitre de « {entry['name']} ».")
            else:
                decide(decisions, entry, "chapter_number", item.chapter_number,
                       f"numéro peu sûr accepté : {guess.get('reason') or 'meilleure proposition'}")  # fmt: skip
        chosen.append(item)
    if not chosen:
        errors.append("Aucun fichier à importer.")
    numbers = [item.chapter_number for item in chosen if item.chapter_number is not None]
    for number in sorted({n for n in numbers if numbers.count(n) > 1}):
        errors.append(f"Le chapitre {number:g} apparaît plusieurs fois dans ce lot.")
    if errors:
        raise Rejected(errors)
    series = resolve_series(db, body.destination, user, "webnovel", body.settings)
    project, created = text_volume(db, series, target, body.settings, user)
    chosen.sort(
        key=lambda item: (item.chapter_number is None, item.chapter_number or 0, natural_key(entries[item.index]["name"]))
    )
    adapter = chapter_adapter(session.format, settings().text_chapter_max_chars)
    size = passage_chars(project, body.settings.passage_max_chars)
    chapters, replace = [], set()
    for position, item in enumerate(chosen):
        name = entries[item.index]["name"]
        key = f"{item.chapter_number:g}" if item.chapter_number is not None else f"name:{name}"
        try:
            chapter = adapter.parse(
                name,
                (staging_dir(session) / f"{item.index}.bin").read_bytes(),
                title=item.title.strip() or entries[item.index]["title"],
                number=item.chapter_number,
                resource=text_resource(project, session.format, key),
                first_line_title=body.first_line_title,
                max_chars=size,
            )
        except TextRejected as exc:
            raise Rejected([f"« {name} » : {exc}"]) from None
        chapters.append(chapter)
        if item.replace:
            replace.add(position)
    outcomes = add_chapters(db, project, chapters, files, replace=replace, discard_human=body.discard_human)
    if not db.scalar(select(Chapter.id).where(Chapter.project_id == project.id).limit(1)):
        raise Rejected(["Aucun chapitre à importer."])
    refresh_series(db, series.id)
    counts = {status: sum(1 for o in outcomes if o.status == status) for status in ("created", "unchanged", "replaced")}
    return {
        "series_id": series.id,
        "series_name": series.name,
        "projects": [
            {
                "id": project.id,
                "title": project.title,
                "volume_number": project.volume_number,
                "status": "created" if created else "updated",
            }
        ],
        "chapters": {
            **counts,
            "items": [
                {"index": item.index, "chapter_id": outcome.chapter_id, "status": outcome.status}
                for item, outcome in zip(chosen, outcomes, strict=True)
            ],
        },
        "decisions": decisions,
    }


@router.post("/{session_id}/commit")
async def commit(session_id: str, body: CommitInput, request: Request, user: CurrentUser, db: DB):
    if body.settings.provider_id and not db.get(Provider, body.settings.provider_id):
        raise HTTPException(422, "Provider inconnu.")
    result = await run_in_threadpool(commit_session, db, session_id, body, user)
    return localized(request, result)


def commit_session(db, session_id: str, body: CommitInput, user) -> dict:
    session = owned_session(db, session_id, user, lock=True)
    # Same commit sent twice (double click, retried request): same answer, nothing imported again.
    result = session.result or confirm(db, session, body, user)
    projects = [db.get(Project, entry["id"]) for entry in result["projects"]]
    return {**result, "views": project_views(db, [p for p in projects if p], bible=False)}


def confirm(db, session: ImportSession, body: CommitInput, user) -> dict:
    files = Files()
    try:
        result = (commit_epub if session.format == "epub" else commit_txt)(db, session, body, user, files)
        jobs, warnings = [], []
        if body.start != "none":
            for entry in result["projects"]:
                project = db.get(Project, entry["id"])
                try:
                    admit(db, project.owner_id)  # the import is kept; its work waits for a free place
                except QueueRefused as exc:
                    warnings.append(str(exc))
                    continue
                job, reason = launch(db, project, body.start)
                if job:
                    jobs.append({"project_id": project.id, "job_id": job.id})
                if reason:
                    warnings.append(reason)
        result = {**result, "jobs": jobs, "warnings": warnings}
        session.result = result
        db.commit()
    except Rejected as exc:
        db.rollback()
        files.discard()
        raise HTTPException(422, {"message": "L’import ne peut pas être confirmé.", "errors": exc.errors}) from None
    except BaseException:
        db.rollback()
        files.discard()
        raise
    files.committed()
    shutil.rmtree(staging_dir(session), ignore_errors=True)
    return result


@router.delete("/{session_id}")
def discard(session_id: str, user: CurrentUser, db: DB):
    session = owned_session(db, session_id, user, lock=True)
    db.delete(session)
    db.commit()
    shutil.rmtree(staging_dir(session), ignore_errors=True)
    return {"ok": True}


