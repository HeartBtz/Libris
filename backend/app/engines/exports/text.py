"""Plain text of a volume, chapter by chapter, without any Libris marker.

TXT and JSON chapters are rebuilt from their recorded layout (blank lines, indentation, scene breaks);
EPUB chapters give one paragraph per unit. The same rendering serves the TXT and Markdown exports, the
ZIP of chapters, the preview of text chapters and the results of the automation API.
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
ATOMIC = re.compile(r"⟦x\d+⟧")
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


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
    # An EPUB document without any heading is named after its file: that name is not a title.
    untitled: bool = False

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def heading(self) -> str:
        return self.translated_title or ("" if self.untitled else self.title)

    def dump(self) -> dict:
        return {**asdict(self), "checksum": self.checksum}


@dataclass
class Line:
    """One rendered line: `gap` blank lines before it; chapter headings and fixed lines (scene
    breaks, ornaments) flagged for the HTML preview."""

    text: str
    gap: int = 0
    indent: str = ""
    heading: bool = False
    fixed: bool = False


def _units(segments: list[Segment], translated: bool) -> tuple[dict[str, str], int]:
    """Text per original unit (fragments of a long paragraph joined, inline codes still in place) and
    the passages without translation."""
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
    return {key: "".join(text for _, text in sorted(items)) for key, items in parts.items()}, missing


def _paragraph(value: str) -> str:
    """An EPUB unit on one line: images and line breaks become spaces, XHTML indentation goes."""
    return " ".join(plain(ATOMIC.sub(" ", value)).split())


def _epub_lines(chapter: Chapter, segments: list[Segment], texts: dict[str, str]) -> tuple[list[Line], str | None]:
    # Attributes (alt, aria-label) and the document's <title> are not part of the reading text.
    units = [unit for s in segments for unit in s.units if unit.get("kind") != "attribute" and unit.get("tag") != "title"]
    tags = {unit.get("original_id", unit["id"]): unit.get("tag") for unit in units}
    sources, _ = _units(segments, translated=False)
    wanted = " ".join(chapter.title.split()).casefold()
    found: list[Line] = []
    heading = None
    for key in dict.fromkeys(tags):
        value = _paragraph(texts.get(key, ""))
        if not value:
            continue
        # The unit the chapter title was read from names the chapter, in the language exported.
        is_heading = heading is None and tags[key] in HEADINGS and _paragraph(sources[key]).casefold() == wanted
        if is_heading:
            heading = value
        found.append(Line(value, gap=1 if found else 0, heading=is_heading))
    return found, heading


def lines(chapter: Chapter, segments: list[Segment], translated: bool = True) -> tuple[list[Line], str | None, int]:
    """The chapter's lines, its heading (translated when it is) and its passages without translation."""
    texts, missing = _units(segments, translated)
    layout = (chapter.import_meta or {}).get("layout")
    if not layout:
        found, heading = _epub_lines(chapter, segments, texts)
        return found, heading, missing
    found = []
    heading = None
    for item in layout["items"]:
        if item.get("title") and not item.get("emit"):
            heading = plain(texts.get(item["unit"], ""))
            continue
        fixed = "fixed" in item
        value = item["fixed"] if fixed else plain(texts.get(item["unit"], ""))
        if item.get("title"):
            heading = value
        found.append(
            Line(
                value,
                gap=int(item.get("gap", 0)),
                indent=item.get("indent", ""),
                heading=bool(item.get("title")),
                fixed=fixed,
            )
        )
    return found, heading, missing


def render(chapter: Chapter, segments: list[Segment], translated: bool = True) -> tuple[str, str | None, int]:
    """The chapter's text, its heading when it has one, and its passages without translation."""
    found, heading, missing = lines(chapter, segments, translated)
    layout = (chapter.import_meta or {}).get("layout")
    if not layout:
        return "\n\n".join(line.text for line in found) + ("\n" if found else ""), heading, missing
    output: list[str] = []
    for line in found:
        output.extend([""] * line.gap)
        output.append(line.indent + line.text)
    text = "\n".join(output) + "\n" * max(1, int(layout.get("trailing_newlines", 1)))
    return text, heading, missing


def reparse_options(chapter: Chapter, segments: list[Segment]) -> tuple[str, bool]:
    """The `title` and `first_line_title` that make `text_chapter` cut this text chapter again into the
    same units: the title unit's source text, or the heading read from the first line of the text."""
    items = chapter.import_meta["layout"]["items"]
    if any(item.get("title") and item.get("emit") for item in items):
        return "", True
    unit = next((item["unit"] for item in items if item.get("title")), None)
    sources, _ = _units(segments, translated=False)
    return (sources.get(unit, "") if unit else ""), False


def source_text(chapter: Chapter, segments: list[Segment]) -> str:
    """The source text of a text chapter rebuilt from its units and layout: cut again with
    `reparse_options`, it gives the same units (a hard-wrapped paragraph comes back on one line)."""
    return render(chapter, segments, translated=False)[0]


def chapter_segments(db: Session, project: Project) -> dict[str, list[Segment]]:
    segments: dict[str, list[Segment]] = {}
    for segment in db.scalars(select(Segment).where(Segment.project_id == project.id).order_by(Segment.position)):
        segments.setdefault(segment.chapter_id, []).append(segment)
    return segments


def volume_texts(db: Session, project: Project, translated: bool = True) -> list[ChapterText]:
    chapters = list(
        db.scalars(
            select(Chapter)
            .where(Chapter.project_id == project.id, Chapter.kind.in_(TEXT_KINDS))
            .order_by(Chapter.position)
        )
    )
    segments = chapter_segments(db, project)
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
                untitled=not (chapter.import_meta or {}).get("layout") and chapter.title == chapter.resource,
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


def _body(item: ChapterText) -> tuple[str, bool]:
    """The chapter's text without surrounding blank lines, and whether its first line is its heading."""
    body = item.text.strip("\n")
    first = body.split("\n", 1)[0].strip()
    return body, bool(item.heading.strip()) and first == item.heading.strip()


def consolidated(project: Project, texts: list[ChapterText]) -> str:
    """One text with every chapter under its heading, chapters separated by two blank lines."""
    blocks = [project.title.strip(), ""]
    for item in texts:
        body, titled = _body(item)
        heading = [] if titled or not item.heading.strip() else [item.heading.strip(), ""]
        blocks += ["", *heading, body, ""]
    return "\n".join(blocks).strip("\n") + "\n"


def _markdown_line(line: str) -> str:
    # A line of the novel that starts like a Markdown heading stays text.
    return "\\" + line if line.lstrip().startswith("#") else line


def markdown(project: Project, texts: list[ChapterText]) -> str:
    """`# Volume`, then `## Chapter` above each chapter's paragraphs."""
    blocks = [f"# {project.title.strip()}", ""]
    for index, item in enumerate(texts, 1):
        body, titled = _body(item)
        if titled:
            body = body.split("\n", 1)[1].lstrip("\n") if "\n" in body else ""
        heading = " ".join(item.heading.split()) or str(index)
        blocks += [f"## {heading}", ""]
        if body:
            blocks += ["\n".join(_markdown_line(line) for line in body.split("\n")), ""]
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


def write_chapters(
    archive: zipfile.ZipFile, prefix: str, project: Project, texts: list[ChapterText], with_consolidated: bool = False
) -> None:
    """`<prefix>chapters/<file>` per chapter, `<prefix>manifest.json`, and optionally the single file."""
    paths = [f"chapters/{name}" for name in chapter_filenames(texts)]
    for path, item in zip(paths, texts, strict=True):
        archive.writestr(prefix + path, item.text.encode("utf-8"))
    if with_consolidated:
        archive.writestr(f"{prefix}{safe_filename(project.title)}.txt", consolidated(project, texts).encode("utf-8"))
    archive.writestr(
        prefix + "manifest.json",
        json.dumps(manifest(project, texts, paths), ensure_ascii=False, indent=2).encode("utf-8"),
    )


def chapters_zip(project: Project, texts: list[ChapterText], with_consolidated: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        write_chapters(archive, "", project, texts, with_consolidated)
    return output.getvalue()
