import base64
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from lxml import etree
from sqlalchemy import select
from starlette.background import BackgroundTask

from app.api.common import row
from app.api.projects import discard_book_file, import_book
from app.config import settings
from app.engines.epub import inspect_archive, rebuild
from app.engines.epub.archive import relative_resource, safe_name, xml
from app.engines.epub.check import epubcheck
from app.engines.epub.text import plain, tag
from app.engines.memory.archive import restore_graph
from app.engines.memory.identities import canonical_bible
from app.engines.translation.versions import save_version
from app.models import (
    Chapter,
    CharacterRelation,
    Entity,
    EntityMerge,
    Glossary,
    Memory,
    Segment,
    TranslationVersion,
)
from app.schemas import BatchExportInput, BookBible, GlossaryInput, TranslationResult
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


def translated_epub(project, segments: list[Segment]) -> bytes:
    if any(not segment.translation or segment.retained_source for segment in segments):
        raise HTTPException(409, "Export bloqué : des passages n’ont pas encore de traduction.")
    content = rebuild(
        Path(project.original_path).read_bytes(),
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
    original = Path(project.original_path).read_bytes()
    if format == "bible":
        content, mime, filename = (
            json.dumps(canonical_bible(db, project), ensure_ascii=False, indent=2),
            "application/json",
            "book-bible.json",
        )
    elif format == "project":
        archive = io.BytesIO()
        chapters = list(
            db.scalars(select(Chapter).where(Chapter.project_id == pid).order_by(Chapter.position))
        )
        payload = {
            "schema_version": 1,
            "project": row(project, ("id", "owner_id", "provider_id", "original_path")),
            "chapters": [row(c) for c in chapters],
            "segments": [row(s) for s in segments],
            "glossary": [row(g) for g in db.scalars(select(Glossary).where(Glossary.project_id == pid))],
            "entities": [row(e) for e in db.scalars(select(Entity).where(Entity.project_id == pid))],
            "character_relations": [
                row(r)
                for r in db.scalars(select(CharacterRelation).where(CharacterRelation.project_id == pid))
            ],
            "entity_merges": [
                row(m) for m in db.scalars(select(EntityMerge).where(EntityMerge.project_id == pid))
            ],
            "memories": [row(m) for m in db.scalars(select(Memory).where(Memory.project_id == pid))],
            "versions": [
                row(v)
                for v in db.scalars(select(TranslationVersion).join(Segment).where(Segment.project_id == pid))
            ],
        }
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            output.writestr("original.epub", original)
            output.writestr("project.json", json.dumps(payload, ensure_ascii=False))
        content, mime, filename = archive.getvalue(), "application/zip", "translation-project.zip"
    elif format == "epub":
        if allow_source:
            export_rows = [row(s) for s in segments]
            for value in export_rows:
                if not value["translated_units"]:
                    value["translated_units"] = [{"id": u["id"], "text": u["text"]} for u in value["units"]]
            content = validated_epub(
                rebuild(original, export_rows, project.target_language, project.title, project.author),
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
    if len(data) > settings().max_upload_mb * 1024**2:
        raise HTTPException(413, "Archive projet trop volumineuse.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) != 2 or {safe_name(i.filename) for i in infos} != {"original.epub", "project.json"}:
            raise ValueError("Archive projet invalide.")
        if sum(i.file_size for i in infos) > settings().max_unpacked_mb * 1024**2:
            raise ValueError("Archive projet trop volumineuse après décompression.")
        original = archive.read("original.epub")
        payload = json.loads(archive.read("project.json"))
    if payload.get("schema_version") != 1:
        raise ValueError("Version de projet non prise en charge.")
    # Reparse the original; never trust imported paths, owners, permissions, jobs or DOM anchors.
    project = import_book(db, user.id, original)
    try:
        restore_project_payload(db, project, payload, user)
        db.commit()
    except BaseException:
        discard_book_file(project)
        raise
    return {"id": project.id, "title": project.title}


def restore_project_payload(db, project, payload: dict, user) -> None:
    info = payload["project"]
    project.target_language = str(info.get("target_language", "fr"))[:80]
    project.series_name = str(info.get("series_name", ""))[:500]
    volume_number = info.get("volume_number")
    project.volume_number = volume_number if isinstance(volume_number, int) and 1 <= volume_number <= 10000 else None
    project.instructions = str(info.get("instructions", ""))[:20000]
    project.bible = BookBible.model_validate(info.get("bible", {})).model_dump()
    project.bible_validated = bool(info.get("bible_validated"))
    if info.get("quality") in {"fast", "normal", "high", "maximum"}:
        project.quality = info["quality"]
    if info.get("context_backend") in {"internal", "openviking", "hybrid"}:
        project.context_backend = info["context_backend"]
    for saved in payload.get("chapters", []):
        chapter = db.scalar(
            select(Chapter).where(
                Chapter.project_id == project.id,
                Chapter.resource == saved.get("resource"),
                Chapter.position == saved.get("position"),
            )
        )
        if chapter:
            chapter.summary = saved.get("summary", {})
            chapter.instructions = str(saved.get("instructions", ""))[:10000]
            chapter.analyzed = bool(saved.get("analyzed"))
    current = project_segments(db, project.id)
    imported = payload.get("segments", [])
    if len(current) != len(imported):
        raise ValueError("Structure du projet incompatible avec son EPUB original.")
    mapping = {}
    for segment, saved in zip(current, imported, strict=True):
        if segment.source != saved.get("source"):
            raise ValueError("Le texte source du projet ne correspond pas à l’EPUB.")
        mapping[saved["id"]] = segment.id
        if saved.get("translated_units"):
            units = [u.model_dump() for u in TranslationResult(units=saved["translated_units"]).units]
            save_version(
                db,
                segment.id,
                units,
                "source_retained"
                if saved.get("retained_source")
                else "restore"
                if saved.get("human")
                else "imported",
                0,
                author_id=user.id,
                validated=bool(saved.get("validated")),
                stage=saved.get("stage")
                if saved.get("stage") in {"translated", "reviewed", "revised", "polished", "done"}
                else "translated",
            )
        segment.instructions = str(saved.get("instructions", ""))[:10000]
    for term in payload.get("glossary", []):
        value = GlossaryInput.model_validate(
            {k: v for k, v in term.items() if k in GlossaryInput.model_fields}
        )
        db.add(Glossary(project_id=project.id, **value.model_dump()))
    restore_graph(db, project.id, payload, mapping)
    for version in payload.get("versions", []):
        if version.get("segment_id") in mapping:
            db.add(
                TranslationVersion(
                    segment_id=mapping[version["segment_id"]],
                    units=version["units"],
                    origin="imported_history",
                    base_revision=0,
                    applied=False,
                )
            )
    for memory in payload.get("memories", []):
        sid = mapping.get(memory.get("segment_id"))
        if sid:
            db.add(
                Memory(
                    project_id=project.id,
                    segment_id=sid,
                    position=int(memory["position"]),
                    kind=str(memory["kind"])[:30],
                    content=memory["content"],
                    validated=bool(memory.get("validated")),
                )
            )


@router.get("/projects/{pid}/preview/{chapter_id}")
def preview(pid: str, chapter_id: str, user: CurrentUser, db: DB, translated: bool = True):
    project = access(db, pid, user)
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != pid:
        raise HTTPException(404, "Chapitre introuvable.")
    original = Path(project.original_path).read_bytes()
    if translated:
        rows = []
        for segment in project_segments(db, pid):
            value = row(segment)
            if not segment.translated_units:
                value["translated_units"] = [{"id": u["id"], "text": u["text"]} for u in segment.units]
            rows.append(value)
        original = rebuild(original, rows, project.target_language)
    entries = inspect_archive(original)
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
