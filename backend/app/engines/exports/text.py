"""Plain text of a volume, chapter by chapter, without any Libris marker.

TXT and JSON chapters are rebuilt from their recorded layout (blank lines, indentation, scene breaks);
EPUB chapters give one paragraph per unit. The same rendering serves the TXT exports, the ZIP of
chapters and the results of the automation API.
"""

import hashlib
import io
import json
import re
import time
import unicodedata
import zipfile
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.epub.text import plain
from app.models import Chapter, Project, Segment

TEXT_KINDS = ("narrative", "auxiliary")
MANIFEST_VERSION = 1


@dataclass
class ChapterText:
    chapter_id: str
    position: int
    number: float | None
    external_id: str | None
    title: str
    translated_title: str | None
    text: str
    complete: bool
    missing_segments: int
    segments: int
    validated: int
    flagged: int
    source_checksum: str | None

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def heading(self) -> str:
        return self.translated_title or self.title

    def dump(self) -> dict:
        return {**asdict(self), "checksum": self.checksum}


def _units(segments: list[Segment], translated: bool) -> tuple[dict[str, str], int]:
    """Text per original unit (fragments of a long paragraph joined), and the passages without translation."""
    parts: dict[str, list[tuple[int, str]]] = {}
    missing = 0
    for segment in segments:
        done = bool(segment.translation) and not segment.retained_source
        if translated and not done:
            missing += 1
        values = {u["id"]: u["text"] for u in segment.translated_units} if translated and done else {}
        for unit in segment.units:
            text = values.get(unit["id"], unit["text"])
            parts.setdefault(unit.get("original_id", unit["id"]), []).append((unit.get("part", 0), text))
    return {key: plain("".join(text for _, text in sorted(items))) for key, items in parts.items()}, missing


def render(chapter: Chapter, segments: list[Segment], translated: bool = True) -> tuple[str, str | None, int]:
    """The chapter's text, its translated heading when it has one, and its passages without translation."""
    texts, missing = _units(segments, translated)
    layout = (chapter.import_meta or {}).get("layout")
    if not layout:
        order = [unit.get("original_id", unit["id"]) for s in segments for unit in s.units if unit.get("kind") != "attribute"]
        paragraphs = [texts[key].strip() for key in dict.fromkeys(order) if texts.get(key, "").strip()]
        return "\n\n".join(paragraphs) + ("\n" if paragraphs else ""), None, missing
    lines: list[str] = []
    heading = None
    for item in layout["items"]:
        if item.get("title") and not item.get("emit"):
            heading = texts.get(item["unit"])
            continue
        lines.extend([""] * int(item.get("gap", 0)))
        value = item["fixed"] if "fixed" in item else texts.get(item["unit"], "")
        if item.get("title"):
            heading = value
        lines.append(item.get("indent", "") + value)
    text = "\n".join(lines) + "\n" * max(1, int(layout.get("trailing_newlines", 1)))
    return text, heading, missing


def volume_texts(db: Session, project: Project, translated: bool = True) -> list[ChapterText]:
    chapters = list(
        db.scalars(
            select(Chapter)
            .where(Chapter.project_id == project.id, Chapter.kind.in_(TEXT_KINDS))
            .order_by(Chapter.position)
        )
    )
    segments: dict[str, list[Segment]] = {}
    for segment in db.scalars(select(Segment).where(Segment.project_id == project.id).order_by(Segment.position)):
        segments.setdefault(segment.chapter_id, []).append(segment)
    result = []
    for chapter in chapters:
        rows = segments.get(chapter.id, [])
        text, heading, missing = render(chapter, rows, translated)
        result.append(
            ChapterText(
                chapter_id=chapter.id,
                position=chapter.position,
                number=chapter.chapter_number,
                external_id=chapter.external_id,
                title=chapter.title,
                translated_title=heading if translated else None,
                text=text,
                complete=missing == 0,
                missing_segments=missing,
                segments=len(rows),
                validated=sum(1 for s in rows if s.validated),
                flagged=sum(1 for s in rows if s.status in {"check", "error", "refused"} and not s.validated),
                source_checksum=chapter.source_checksum,
            )
        )
    return result


def safe_filename(value: str, limit: int = 120) -> str:
    """A portable file name: no path separators, control or reserved characters."""
    value = unicodedata.normalize("NFC", value)
    value = re.sub(r"[\x00-\x1f\x7f<>:\"/\\|?*]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:limit].rstrip(" .") or "chapitre"


def chapter_filenames(texts: list[ChapterText]) -> list[str]:
    """`001 - Title.txt`: numbers padded to the longest, in reading order, never twice the same name."""
    labels = []
    for index, item in enumerate(texts, 1):
        number = item.number if item.number is not None else index
        labels.append(f"{number:g}" if not float(number).is_integer() else str(int(number)))
    width = max((len(label.split(".")[0]) for label in labels), default=1)
    used: set[str] = set()
    names = []
    for label, item in zip(labels, texts, strict=True):
        integer, _, decimals = label.partition(".")
        padded = integer.zfill(max(3, width)) + (f".{decimals}" if decimals else "")
        base = f"{padded} - {safe_filename(item.heading)}"
        name, suffix = f"{base}.txt", 2
        while name.casefold() in used:
            name, suffix = f"{base} ({suffix}).txt", suffix + 1
        used.add(name.casefold())
        names.append(name)
    return names


def consolidated(project: Project, texts: list[ChapterText]) -> str:
    """One text with every chapter under its heading, separated by blank lines."""
    blocks = [project.title.strip(), ""]
    for item in texts:
        body = item.text.strip("\n")
        # A heading already written as the chapter's first line is not repeated.
        first = body.split("\n", 1)[0].strip()
        blocks += ([] if first == item.heading.strip() else [item.heading, ""]) + [body, "", ""]
    return "\n".join(blocks).rstrip("\n") + "\n"


def manifest(project: Project, texts: list[ChapterText], names: list[str] | None = None) -> dict:
    return {
        "schema_version": MANIFEST_VERSION,
        "generated_at": time.time(),
        "title": project.title,
        "series": project.series_name or None,
        "volume_number": project.volume_number,
        "source_format": project.source_format,
        "source_language": project.source_language,
        "target_language": project.target_language,
        "complete": all(item.complete for item in texts),
        "chapters": [
            {
                "file": names[index] if names else None,
                "position": item.position,
                "number": item.number,
                "external_id": item.external_id,
                "title": item.title,
                "translated_title": item.translated_title,
                "complete": item.complete,
                "missing_segments": item.missing_segments,
                "sha256": item.checksum,
                "source_sha256": item.source_checksum,
            }
            for index, item in enumerate(texts)
        ],
    }


def chapters_zip(project: Project, texts: list[ChapterText], with_consolidated: bool = False) -> bytes:
    paths = [f"chapters/{name}" for name in chapter_filenames(texts)]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, item in zip(paths, texts, strict=True):
            archive.writestr(path, item.text.encode("utf-8"))
        if with_consolidated:
            archive.writestr(f"{safe_filename(project.title)}.txt", consolidated(project, texts).encode("utf-8"))
        archive.writestr(
            "manifest.json", json.dumps(manifest(project, texts, paths), ensure_ascii=False, indent=2).encode("utf-8")
        )
    return output.getvalue()
