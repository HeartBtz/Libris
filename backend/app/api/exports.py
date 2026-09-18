import base64
import json
import os
import re
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from lxml import etree
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.api.common import row
from app.api.project_archive import build_archive, read_archive, restore_archive
from app.api.projects import discard_book_file, import_book
from app.config import settings
from app.engines.epub import inspect_archive, rebuild
from app.engines.epub.archive import relative_resource, xml
from app.engines.epub.check import epubcheck
from app.engines.epub.text import plain, tag
from app.engines.memory.identities import canonical_bible
from app.models import Chapter, Segment
from app.schemas import BatchExportInput
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api")


def project_segments(db, pid: str) -> list[Segment]:
    return list(db.scalars(select(Segment).where(Segment.project_id == pid).order_by(Segment.position)))


def validated_epub(content: bytes, title: str) -> bytes:
    validation = epubcheck(content)
    if validation["available"] and not validation["valid"]:
        failures = [
            m
            for m in validation["report"].get("messages", [])
            if m.get("severity") in {"ERROR", "FATAL"}
        ]
        errors = [
            f"{m.get('ID', '')} — {str(m.get('message', ''))[:300]}"
            + "".join(f" ({place['path']})" for place in m.get("locations", [])[:1] if place.get("path"))
            for m in failures[:5]
        ]
        if len(failures) > 5:
            errors.append(f"… et {len(failures) - 5} autre(s) erreur(s).")
        raise HTTPException(
            422,
            {
                "message": f"EPUBCheck signale un EPUB invalide : « {title} ».",
                "book": title,
                "errors": errors,
                "validation": validation,
            },
        )
    return content


def original_bytes(project) -> bytes:
    # The stored path is absolute; the file name is deterministic, so a data directory restored
    # elsewhere is still found.
    for path in (Path(project.original_path), settings().data_dir / "books" / f"{project.id}.epub"):
        if path.is_file():
            return path.read_bytes()
    raise HTTPException(
        409,
        f"Le fichier EPUB d’origine de « {project.title} » est introuvable sur le serveur : l’EPUB, "
        "l’archive de projet et l’aperçu sont indisponibles. Les exports TXT, Markdown et Book Bible "
        "restent possibles ; restaurez le dossier des livres (DATA_DIR/books) pour retrouver les autres.",
    )


def translated_epub(project, segments: list[Segment]) -> bytes:
    if any(not segment.translation or segment.retained_source for segment in segments):
        raise HTTPException(409, "Export bloqué : des passages n’ont pas encore de traduction.")
    content = rebuild(
        original_bytes(project),
        [row(segment) for segment in segments],
        project.target_language,
        project.title,
        project.author,
    )
    return validated_epub(content, project.title)


def unique_epub_name(title: str, used: set[str]) -> str:
    base = re.sub(r"[^\w .()#-]", "_", title, flags=re.UNICODE).strip(" .")[:180] or "livre"
    name, number = f"{base}.epub", 2
    while name.casefold() in used:
        name = f"{base} ({number}).epub"
        number += 1
    used.add(name.casefold())
    return name


@router.post("/exports/epub")
def export_epubs(body: BatchExportInput, user: CurrentUser, db: DB):
    if len(set(body.project_ids)) != len(body.project_ids):
        raise HTTPException(422, "La sélection contient des projets en double.")
    selected = []
    incomplete = []
    for project_id in body.project_ids:
        project = access(db, project_id, user)
        segments = project_segments(db, project_id)
        selected.append((project, segments))
        if any(not segment.translation or segment.retained_source for segment in segments):
            incomplete.append(project.title)
    if incomplete:
        raise HTTPException(
            409,
            "Export bloqué, traduction incomplète : " + ", ".join(incomplete),
        )

    descriptor, archive_path = tempfile.mkstemp(prefix="libris-epubs-", suffix=".zip")
    os.close(descriptor)
    try:
        used: set[str] = set()
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
            for project, segments in selected:
                archive.writestr(unique_epub_name(project.title, used), translated_epub(project, segments))
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename="libris-epubs.zip",
            background=BackgroundTask(os.unlink, archive_path),
        )
    except Exception:
        Path(archive_path).unlink(missing_ok=True)
        raise


@router.get("/projects/{pid}/export/{format}")
def export(
    pid: str,
    format: Literal["epub", "txt", "md", "bible", "project"],
    user: CurrentUser,
    db: DB,
    allow_source: bool = False,
):
    project = access(db, pid, user)
    segments = project_segments(db, pid)
    if format == "bible":
        content, mime, filename = (
            json.dumps(canonical_bible(db, project), ensure_ascii=False, indent=2),
            "application/json",
            "book-bible.json",
        )
    elif format == "project":
        content, mime, filename = (
            build_archive(db, project, original_bytes(project)),
            "application/zip",
            "translation-project.zip",
        )
    elif format == "epub":
        if allow_source:
            export_rows = [row(s) for s in segments]
            for value in export_rows:
                if not value["translated_units"]:
                    value["translated_units"] = [{"id": u["id"], "text": u["text"]} for u in value["units"]]
            content = validated_epub(
                rebuild(
                    original_bytes(project),
                    export_rows,
                    project.target_language,
                    project.title,
                    project.author,
                ),
                project.title,
            )
        else:
            content = translated_epub(project, segments)
        mime, filename = (
            "application/epub+zip",
            "translated-partial-with-originals.epub" if allow_source else "translated.epub",
        )
    else:
        if any(not s.translation for s in segments):
            raise HTTPException(409, "La traduction n’est pas encore complète.")
        content = "\n\n".join(plain(s.translation) for s in segments)
        if format == "md":
            content = f"# {project.title}\n\n{content}"
        mime, filename = "text/plain; charset=utf-8", f"translation.{format}"
    return Response(
        content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/projects/import", status_code=201)
async def restore_project(file: UploadFile, user: CurrentUser, db: DB):
    data = await file.read(settings().max_upload_mb * 1024**2 + 1)
    # Unpacking, parsing the book and writing thousands of rows must not freeze the event loop.
    project = await run_in_threadpool(restore_from_archive, db, user.id, data)
    return {"id": project.id, "title": project.title}


def restore_from_archive(db, owner_id: str, data: bytes):
    original, archive = read_archive(data)
    # Reparse the original; never trust imported paths, owners, permissions, providers or DOM anchors.
    # An archive made before segmentation 2 must be cut as it was, or its passages would not match.
    project = import_book(db, owner_id, original, archive.project.book_info.get("segmentation", 1))
    try:
        restore_archive(db, project, archive)
        db.commit()
    except BaseException:
        discard_book_file(project)
        raise
    return project


class PreviewCache:
    """Unpacked books of the latest previews, bounded in bytes.

    Each chapter preview used to rebuild and unpack the whole book. The key carries the sum of the
    passages' revisions, which every saved version increments: an edit is never served stale.
    """

    def __init__(self, entries: int = 8):
        self.lock = threading.Lock()
        self.books: OrderedDict[tuple, tuple[dict[str, bytes], int]] = OrderedDict()
        self.entries = entries

    def get(self, key: tuple) -> dict[str, bytes] | None:
        with self.lock:
            if key in self.books:
                self.books.move_to_end(key)
                return self.books[key][0]
        return None

    def put(self, key: tuple, book: dict[str, bytes]) -> None:
        budget = settings().preview_cache_mb * 1024**2
        size = sum(len(value) for value in book.values())
        if size > budget:
            return
        with self.lock:
            self.books.pop(key, None)
            self.books[key] = (book, size)
            while len(self.books) > self.entries or sum(s for _, s in self.books.values()) > budget:
                self.books.popitem(last=False)


previews = PreviewCache()


def preview_entries(db, project, translated: bool) -> dict[str, bytes]:
    revisions = db.scalar(
        select(func.coalesce(func.sum(Segment.revision), 0)).where(Segment.project_id == project.id)
    )
    language = project.target_language if translated else ""
    key = (project.id, project.original_hash, translated, language, revisions)
    cached = previews.get(key)
    if cached is not None:
        return cached
    original = original_bytes(project)
    if translated:
        rows = []
        for segment in project_segments(db, project.id):
            value = row(segment)
            if not segment.translated_units:
                value["translated_units"] = [{"id": u["id"], "text": u["text"]} for u in segment.units]
            rows.append(value)
        original = rebuild(original, rows, project.target_language)
    entries = inspect_archive(original)
    previews.put(key, entries)
    return entries


@router.get("/projects/{pid}/preview/{chapter_id}")
def preview(pid: str, chapter_id: str, user: CurrentUser, db: DB, translated: bool = True):
    project = access(db, pid, user)
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != pid:
        raise HTTPException(404, "Chapitre introuvable.")
    entries = preview_entries(db, project, translated)
    root = xml(entries[chapter.resource])
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        if tag(node) in {
            "script",
            "iframe",
            "object",
            "embed",
            "link",
            "meta",
            "style",
            "svg",
            "form",
            "base",
        }:
            if node.getparent() is not None:
                node.getparent().remove(node)
            continue
        for attribute in list(node.attrib):
            if attribute not in {"class", "id", "alt", "title", "colspan", "rowspan", "lang", "src", "href"}:
                del node.attrib[attribute]
        if node.get("href") and not node.get("href", "").startswith("#"):
            del node.attrib["href"]
        if node.get("src"):
            try:
                path = relative_resource(chapter.resource, node.get("src"))
                extension = path.rsplit(".", 1)[-1].lower()
                if extension not in {"png", "jpg", "jpeg", "gif", "webp"} or path not in entries:
                    raise ValueError()
                node.set(
                    "src",
                    f"data:image/{'jpeg' if extension == 'jpg' else extension};base64,"
                    + base64.b64encode(entries[path]).decode(),
                )
            except ValueError:
                del node.attrib["src"]
    body = root.xpath("//*[local-name()='body']")
    content = etree.tostring(body[0] if body else root, encoding="unicode", method="html")
    return {
        "html": '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
        "<style>body{max-width:46em;margin:3em auto;padding:1em;line-height:1.7;font-family:Georgia,serif;"
        "color:#222;background:#fff}img{max-width:100%}p{overflow-wrap:anywhere}</style></head>"
        + content
        + "</html>",
        "simplified": True,
    }
