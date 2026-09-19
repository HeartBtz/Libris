import base64
import html
import json
import os
import re
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from lxml import etree
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from app.api.common import row
from app.api.project_archive import (
    LEGACY_EPUB,
    build_archive,
    read_archive,
    restore_archive,
    restore_series,
    restore_text_volume,
)
from app.api.projects import discard_book_file, import_book
from app.config import settings
from app.engines.epub import inspect_archive, rebuild
from app.engines.epub.archive import relative_resource, xml
from app.engines.epub.check import epubcheck
from app.engines.epub.text import tag
from app.engines.exports.text import (
    ChapterText,
    chapters_zip,
    consolidated,
    lines,
    markdown,
    safe_filename,
    volume_texts,
    write_chapters,
)
from app.engines.ingestion.store import Files, primary_asset, read_asset
from app.engines.memory.identities import canonical_bible
from app.models import Chapter, Segment
from app.schemas import BatchExportInput, TextBatchExportInput
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


def not_epub_message(title: str) -> str:
    return (
        f"« {title} » a été importé depuis des fichiers texte, pas depuis un EPUB : exportez-le en TXT, "
        "en ZIP de chapitres ou en Markdown."
    )


def original_bytes(db, project) -> bytes:
    """The EPUB the volume was imported from: its source file row, then, for books imported before
    0.6, the absolute path they recorded and their deterministic place under DATA_DIR/books (a data
    directory restored elsewhere is still found)."""
    if project.source_format != "epub":
        raise HTTPException(409, not_epub_message(project.title))
    asset = primary_asset(db, project)
    content = read_asset(asset) if asset else None
    if content is not None:
        return content
    legacy = [Path(project.original_path)] if project.original_path else []
    for path in (*legacy, settings().data_dir / "books" / f"{project.id}.epub"):
        if path.is_file():
            return path.read_bytes()
    raise HTTPException(
        409,
        f"Le fichier EPUB d’origine de « {project.title} » est introuvable sur le serveur : l’EPUB, "
        "l’archive de projet et l’aperçu sont indisponibles. Les exports TXT, Markdown et Book Bible "
        "restent possibles ; restaurez le dossier des livres (DATA_DIR/books) pour retrouver les autres.",
    )


def untranslated(segment: Segment) -> bool:
    """A passage kept in its source on purpose (source_retained) is decided, not missing."""
    return not segment.translation and not segment.retained_source


def export_row(segment: Segment) -> dict:
    value = row(segment)
    if segment.retained_source:
        value["translated_units"] = [{"id": u["id"], "text": u["text"]} for u in segment.units]
    return value


def translated_epub(db, project, segments: list[Segment]) -> bytes:
    if project.source_format != "epub":
        raise HTTPException(409, not_epub_message(project.title))
    if any(untranslated(segment) for segment in segments):
        raise HTTPException(409, "Export bloqué : des passages n’ont pas encore de traduction.")
    content = rebuild(
        original_bytes(db, project),
        [export_row(segment) for segment in segments],
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
        if any(untranslated(segment) for segment in segments):
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
                archive.writestr(unique_epub_name(project.title, used), translated_epub(db, project, segments))
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename="libris-epubs.zip",
            background=BackgroundTask(os.unlink, archive_path),
        )
    except Exception:
        Path(archive_path).unlink(missing_ok=True)
        raise


def chapter_range(texts: list[ChapterText], first: float | None, last: float | None) -> list[ChapterText]:
    """Chapters numbered from `first` to `last` (both included): the new chapters of a follow-up.
    Unnumbered chapters are kept only when no bound is given."""
    if first is None and last is None:
        return texts
    if first is not None and last is not None and first > last:
        raise HTTPException(422, "Le premier chapitre doit précéder le dernier.")
    chosen = [
        item for item in texts
        if item.number is not None
        and (first is None or item.number >= first)
        and (last is None or item.number <= last)
    ]  # fmt: skip
    if not chosen:
        raise HTTPException(404, "Aucun chapitre de ce volume dans cet intervalle.")
    return chosen


def exported_texts(
    db, project, allow_source: bool, first: float | None = None, last: float | None = None
) -> list[ChapterText]:
    texts = chapter_range(volume_texts(db, project), first, last)
    if not allow_source and not all(item.complete for item in texts):
        raise HTTPException(409, "La traduction n’est pas encore complète.")
    return texts


@router.post("/exports/text")
def export_texts(body: TextBatchExportInput, user: CurrentUser, db: DB):
    """Several volumes (a series) as one ZIP: a folder per volume, its chapters and their manifest."""
    if len(set(body.project_ids)) != len(body.project_ids):
        raise HTTPException(422, "La sélection contient des projets en double.")
    projects = [access(db, project_id, user) for project_id in body.project_ids]
    selected, incomplete = [], []
    for project in projects:
        texts = volume_texts(db, project)
        selected.append((project, texts))
        if not all(item.complete for item in texts):
            incomplete.append(project.title)
    if incomplete and not body.allow_source:
        raise HTTPException(409, "Export bloqué, traduction incomplète : " + ", ".join(incomplete))
    descriptor, archive_path = tempfile.mkstemp(prefix="libris-texts-", suffix=".zip")
    os.close(descriptor)
    try:
        used: set[str] = set()
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for project, texts in sorted(selected, key=lambda item: (item[0].volume_number is None,
                                                                      item[0].volume_number or 0)):
                label = f"{project.volume_number:02d} - {project.title}" if project.volume_number else project.title
                folder, number = safe_filename(label), 2
                while folder.casefold() in used:
                    folder, number = f"{safe_filename(label)} ({number})", number + 1
                used.add(folder.casefold())
                write_chapters(archive, f"{folder}/", project, texts, body.consolidated)
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename="libris-textes.zip",
            background=BackgroundTask(os.unlink, archive_path),
        )
    except Exception:
        Path(archive_path).unlink(missing_ok=True)
        raise


@router.get("/projects/{pid}/export/{format}")
def export(
    pid: str,
    format: Literal["epub", "txt", "txt-zip", "md", "bible", "project"],
    user: CurrentUser,
    db: DB,
    allow_source: bool = False,
    consolidated_text: bool = Query(False, alias="consolidated"),
    from_chapter: float | None = Query(None, ge=0, le=100000),
    to_chapter: float | None = Query(None, ge=0, le=100000),
):
    """`txt`: every chapter under its heading in one file; `txt-zip`: one UTF-8 file per chapter and
    a manifest with checksums (plus the single file with `consolidated=true`); `md`: Markdown with a
    `##` heading per chapter. `allow_source=true` exports an unfinished translation, originals kept.
    `from_chapter` / `to_chapter` limit the text formats to a range of chapter numbers."""
    project = access(db, pid, user)
    if format == "bible":
        content, mime, filename = (
            json.dumps(canonical_bible(db, project), ensure_ascii=False, indent=2),
            "application/json",
            "book-bible.json",
        )
    elif format == "project":
        content, mime, filename = (
            build_archive(db, project, original_bytes(db, project) if project.source_format == "epub" else None),
            "application/zip",
            "translation-project.zip",
        )
    elif format == "epub":
        segments = project_segments(db, pid)
        if project.source_format != "epub":
            raise HTTPException(409, not_epub_message(project.title))
        if allow_source:
            export_rows = [row(s) for s in segments]
            for value in export_rows:
                if not value["translated_units"]:
                    value["translated_units"] = [{"id": u["id"], "text": u["text"]} for u in value["units"]]
            content = validated_epub(
                rebuild(
                    original_bytes(db, project),
                    export_rows,
                    project.target_language,
                    project.title,
                    project.author,
                ),
                project.title,
            )
        else:
            content = translated_epub(db, project, segments)
        mime, filename = (
            "application/epub+zip",
            "translated-partial-with-originals.epub" if allow_source else "translated.epub",
        )
    else:
        texts = exported_texts(db, project, allow_source, from_chapter, to_chapter)
        partial = "-partial-with-originals" if allow_source and not all(item.complete for item in texts) else ""
        if format == "txt-zip":
            content, mime, filename = (
                chapters_zip(project, texts, with_consolidated=consolidated_text),
                "application/zip",
                f"translation-chapters{partial}.zip",
            )
        elif format == "md":
            content, mime, filename = markdown(project, texts), "text/markdown; charset=utf-8", f"translation{partial}.md"
        else:
            content, mime, filename = consolidated(project, texts), "text/plain; charset=utf-8", f"translation{partial}.txt"
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
    files, archive = read_archive(data)
    # Never trust imported paths, owners, permissions, providers or DOM anchors: the sources are cut
    # again here and the saved passages must match them.
    series = restore_series(db, owner_id, archive)
    written = Files()
    project = None
    try:
        legacy = archive.schema_version < 3
        if legacy or archive.project.source_format == "epub":
            source = None if legacy else next(item for item in archive.sources if item.format == "epub")
            # An archive made before segmentation 2 must be cut as it was, or its passages would not match.
            project = import_book(
                db,
                owner_id,
                files[source.file] if source else files[LEGACY_EPUB],
                archive.project.book_info.get("segmentation", 1),
                name=source.original_name if source else "book.epub",
                series=series,
                volume_number=archive.project.volume_number,
                files=written,
            )
        else:
            project = restore_text_volume(db, owner_id, archive, files, series, written)
        project.provider_id = None
        restore_archive(db, project, archive)
        db.commit()
    except BaseException:
        written.discard()
        if project is not None:
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
    original = original_bytes(db, project)
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
    if (chapter.import_meta or {}).get("layout"):
        return preview_page(text_preview(db, chapter, translated))
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
    return preview_page(etree.tostring(body[0] if body else root, encoding="unicode", method="html"))


def preview_page(body: str) -> dict:
    return {
        "html": '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; img-src data:; style-src 'unsafe-inline'\">"
        "<style>body{max-width:46em;margin:3em auto;padding:1em;line-height:1.7;font-family:Georgia,serif;"
        "color:#222;background:#fff}img{max-width:100%}p{overflow-wrap:anywhere}"
        "p.text{white-space:pre-wrap}p.break{text-align:center}</style></head>"
        + body
        + "</html>",
        "simplified": True,
    }


def text_preview(db, chapter: Chapter, translated: bool) -> str:
    """A TXT or JSON chapter as simple HTML built from its layout; every text is escaped."""
    segments = list(db.scalars(select(Segment).where(Segment.chapter_id == chapter.id).order_by(Segment.position)))
    found, heading, _ = lines(chapter, segments, translated)
    parts = [] if any(line.heading for line in found) or not heading else [f"<h1>{html.escape(heading)}</h1>"]
    paragraph: list[str] = []

    def close() -> None:
        if paragraph:
            parts.append('<p class="text">' + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    for line in found:
        if line.gap or line.heading or line.fixed:
            close()
        if line.heading:
            parts.append(f"<h1>{html.escape(line.text)}</h1>")
        elif line.fixed:
            parts.append(f'<p class="break">{html.escape(line.text)}</p>')
        else:
            paragraph.append(html.escape(line.indent + line.text))
    close()
    return "<body>" + "".join(parts) + "</body>"
