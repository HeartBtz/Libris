"""Results of automation requests: rendered in the asked format, stored once the request ends.

The artifact is written under DATA_DIR/results/<request id>/ at a name Libris chooses; the request row
records its format, size and checksum. Other formats are rendered on demand from SQL. Passages without
a translation keep their source text in every format, and the completion report lists them.
"""

import hashlib
import io
import json
import shutil
import time
import zipfile
from dataclasses import dataclass

from sqlalchemy import select

from app.config import settings
from app.engines.delivery.epub import DeliveryFailed, check, deliver_epub, describe, failures
from app.engines.exports.bilingual import bilingual_filename, build_bilingual_epub, volume_pairs
from app.engines.exports.text import ChapterText, consolidated, safe_filename, volume_texts, write_chapters
from app.engines.ingestion.payload import SCHEMA_VERSION
from app.engines.ingestion.store import data_path, primary_asset, read_asset
from app.models import Issue, Job, Project, Provider, Segment, Series, TranslationRequest

FORMATS = ("json", "txt", "txt-zip", "epub", "epub-bilingual")
# What a result covers (see request_texts); the stored artifact is the `request` one.
SCOPES = ("request", "new", "volume")
MEDIA_TYPES = {
    "json": "application/json",
    "txt": "text/plain; charset=utf-8",
    "txt-zip": "application/zip",
    "epub": "application/epub+zip",
    "epub-bilingual": "application/epub+zip",
}
EXTENSIONS = {"json": "json", "txt": "txt", "txt-zip": "zip", "epub": "epub", "epub-bilingual": "epub"}
FLAGGED = ("check", "error", "refused")


@dataclass
class Rendered:
    content: bytes
    format: str
    filename: str
    # EPUB only: validation, repairs and passages written with their source.
    delivery: dict | None = None

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[self.format]


def default_format(request: TranslationRequest) -> str:
    """EPUB in, EPUB out; otherwise the format the request asked for."""
    if request.options.get("input") == "epub":
        return request.options.get("output_format") or "epub"
    return request.options.get("output_format") or "json"


def request_texts(
    db, request: TranslationRequest, project: Project, scope: str = "request"
) -> list[ChapterText]:
    """`request`: the chapters the request sent (the whole book for an EPUB); `new`: only those it added
    or replaced; `volume`: every chapter of the volume, earlier deliveries included."""
    texts = volume_texts(db, project)
    if scope == "volume" or (scope == "request" and request.options.get("input") == "epub"):
        return texts
    if scope == "new":
        wanted = set(request.options.get("new_chapter_ids", request.chapter_ids) or [])
    else:
        wanted = set(request.chapter_ids or [])
    return [item for item in texts if item.chapter_id in wanted]


def unresolved(db, chapter_ids: list[str]) -> dict[str, dict]:
    """Per chapter: unresolved quality issues and passages still flagged."""
    found: dict[str, dict] = {
        chapter_id: {"issues": [], "flagged_passages": []} for chapter_id in chapter_ids
    }
    if not chapter_ids:
        return found
    for issue, chapter_id in db.execute(
        select(Issue, Segment.chapter_id)
        .join(Segment, Segment.id == Issue.segment_id)
        .where(Segment.chapter_id.in_(chapter_ids), Issue.resolved.is_(False))
        .order_by(Segment.position)
    ):
        found[chapter_id]["issues"].append(
            {
                "segment_id": issue.segment_id,
                "severity": issue.severity,
                "code": issue.code,
                "message": issue.message,
            }
        )
    for segment_id, chapter_id, position, status in db.execute(
        select(Segment.id, Segment.chapter_id, Segment.position, Segment.status)
        .where(Segment.chapter_id.in_(chapter_ids), Segment.status.in_(FLAGGED), Segment.validated.is_(False))
        .order_by(Segment.position)
    ):
        found[chapter_id]["flagged_passages"].append(
            {"segment_id": segment_id, "position": position, "status": status}
        )
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


def document(
    db, request: TranslationRequest, project: Project, job: Job | None, texts: list[ChapterText], status: str,
    complete: bool, report: dict | None,
) -> dict:  # fmt: skip
    problems = unresolved(db, [item.chapter_id for item in texts])
    series = db.get(Series, project.series_id) if project.series_id else None
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request.id,
        "external_id": request.external_id,
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
        "strategy": strategy(db, project, job, request),
        "incomplete_chapters": [item.external_id or item.chapter_id for item in texts if not item.complete],
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
        "report": report,
    }


def original_epub(db, project: Project) -> bytes | None:
    asset = primary_asset(db, project)
    return read_asset(asset) if asset else None


def render_epub(db, project: Project) -> Rendered:
    """The translated EPUB, residual passages in their source text, repaired when EPUBCheck refuses it."""
    original = original_epub(db, project)
    if original is None:
        raise DeliveryFailed("Le fichier EPUB d’origine de ce volume est introuvable sur le serveur.")
    segments = list(
        db.scalars(select(Segment).where(Segment.project_id == project.id).order_by(Segment.position))
    )
    built = deliver_epub(original, segments, project.target_language, project.title, project.author)
    validation = {key: value for key, value in built.validation.items() if key != "report"}
    return Rendered(
        built.content,
        "epub",
        safe_filename(project.title) + ".epub",
        {
            "validation": validation,
            "repairs": built.repairs,
            "inherited_errors": built.inherited_errors,
            "fallbacks": built.fallbacks,
        },
    )


def render_bilingual(
    db, request: TranslationRequest, project: Project, layout: str, scope: str = "request"
) -> Rendered:
    """Source and translation paragraph by paragraph, for the chapters of `scope` (see request_texts);
    a passage still untranslated (partial result) is marked empty."""
    if scope == "volume" or (scope == "request" and request.options.get("input") == "epub"):
        wanted = None
    elif scope == "new":
        wanted = set(request.options.get("new_chapter_ids", request.chapter_ids) or [])
    else:
        wanted = set(request.chapter_ids or [])
    content = build_bilingual_epub(project, volume_pairs(db, project, wanted), layout)
    blocking = failures(check(content))
    if blocking:
        raise DeliveryFailed(
            "EPUBCheck refuse l’EPUB bilingue de ce volume.", [describe(message) for message in blocking[:5]]
        )
    return Rendered(content, "epub-bilingual", bilingual_filename(project.title))


def render(
    db, request: TranslationRequest, project: Project, job: Job | None, fmt: str, status: str, report: dict | None,
    bundle_report: bool = False, layout: str = "interleaved", scope: str = "request",
) -> Rendered:  # fmt: skip
    """`bundle_report` adds report.json to a ZIP of chapters (the stored artifact carries its report);
    `layout` only applies to the bilingual EPUB."""
    if fmt == "epub":
        return render_epub(db, project)
    if fmt == "epub-bilingual":
        return render_bilingual(db, request, project, layout, scope)
    texts = request_texts(db, request, project, scope)
    complete = (
        bool(texts) and all(item.complete for item in texts) and not (report or {}).get("residual_total")
    )
    name = safe_filename(project.title)
    if fmt == "txt":
        return Rendered(consolidated(project, texts).encode("utf-8"), fmt, name + ".txt")
    if fmt == "txt-zip":
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            write_chapters(archive, "", project, texts)
            if bundle_report and report is not None:
                archive.writestr(
                    "report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
                )
        return Rendered(output.getvalue(), fmt, name + ".zip")
    body = document(db, request, project, job, texts, status, complete, report)
    body["scope"] = scope
    return Rendered(json.dumps(body, ensure_ascii=False).encode("utf-8"), "json", name + ".json")


def results_dir(request: TranslationRequest):
    return data_path(f"results/{request.id}")


def store(request: TranslationRequest, rendered: Rendered) -> dict:
    """Writes the artifact at a path chosen here; returns what the request row records about it."""
    folder = results_dir(request)
    folder.mkdir(parents=True, exist_ok=True)
    relative = f"results/{request.id}/result.{EXTENSIONS[rendered.format]}"
    temporary = data_path(relative + ".part")
    temporary.write_bytes(rendered.content)
    temporary.replace(data_path(relative))
    return {
        "path": relative,
        "format": rendered.format,
        "media_type": rendered.media_type,
        "filename": rendered.filename,
        "size": len(rendered.content),
        "sha256": hashlib.sha256(rendered.content).hexdigest(),
        "created_at": time.time(),
    }


def stored(request: TranslationRequest) -> bytes | None:
    artifact = request.artifact or {}
    if not artifact.get("path"):
        return None
    path = data_path(artifact["path"])
    return path.read_bytes() if path.is_file() else None


def discard(request: TranslationRequest) -> None:
    shutil.rmtree(results_dir(request), ignore_errors=True)
