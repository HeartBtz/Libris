"""Plain text chapters (TXT files, JSON payloads) turned into the units the pipeline translates.

Only the encoding, line endings and forbidden control characters are normalized. Every other
character is kept; the layout of the text (blank lines, indentation, scene separators) is recorded in
`meta["layout"]` so that an export gives back the same shape with the translated paragraphs.
"""

import codecs
import hashlib
import re

from app.engines.epub.text import MARKER, group_units
from app.engines.ingestion.base import (
    FileInspection,
    ImportedAsset,
    ImportedChapter,
    ImportedUnit,
    SourceAdapter,
)
from app.engines.ingestion.naming import CHAPTER_KEYWORD, chapter_from_name, clean_title
from app.engines.ingestion.passages import DEFAULT_PASSAGE_CHARS

# C0 controls except tab and line feed, DEL and C1 controls: never part of a novel's text.
FORBIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
TERMINAL = tuple(".!?…:;\"'»”’)]」』】）—–-~*")
MAX_UNIT_CHARS = DEFAULT_PASSAGE_CHARS


class TextRejected(ValueError):
    pass


def decode(data: bytes) -> tuple[str, str, list[str]]:
    """UTF-8 (with or without BOM) and UTF-16 with BOM; Windows-1252 only as a flagged last resort."""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise TextRejected("Encodage UTF-32 non pris en charge : enregistrez le fichier en UTF-8.")
    try:
        if data.startswith(codecs.BOM_UTF8):
            return data[len(codecs.BOM_UTF8) :].decode("utf-8"), "utf-8-sig", []
        if data.startswith(codecs.BOM_UTF16_LE):
            return data[2:].decode("utf-16-le"), "utf-16-le", []
        if data.startswith(codecs.BOM_UTF16_BE):
            return data[2:].decode("utf-16-be"), "utf-16-be", []
    except UnicodeDecodeError:
        raise TextRejected("Le fichier annonce un encodage (BOM) que son contenu ne respecte pas.") from None
    try:
        return data.decode("utf-8"), "utf-8", []
    except UnicodeDecodeError:
        pass
    if b"\x00" in data:
        raise TextRejected("Encodage non reconnu (UTF-16 sans BOM ?) : enregistrez le fichier en UTF-8.")
    try:
        text = data.decode("cp1252")
    except UnicodeDecodeError:
        raise TextRejected("Encodage non reconnu : enregistrez le fichier en UTF-8.") from None
    return text, "windows-1252", [
        "Ce fichier n’est pas en UTF-8 : il a été lu en Windows-1252. Vérifiez les accents de l’aperçu."
    ]


def normalize(text: str) -> tuple[str, int]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    cleaned, removed = FORBIDDEN.subn("", text)
    return cleaned, removed


def has_words(line: str) -> bool:
    return any(char.isalpha() for char in line)


def wrapped(blocks: list[list[str]]) -> bool:
    """Hard-wrapped prose (fixed-width lines inside blank-line separated paragraphs)."""
    joints = [line.rstrip() for block in blocks for line in block[:-1]]
    if len(joints) < 3:
        return False
    open_ended = sum(1 for line in joints if line and not line.endswith(TERMINAL))
    return open_ended / len(joints) >= 0.7


def paragraphs(text: str) -> tuple[str, list[dict]]:
    """Layout items in reading order: {"text"} paragraphs or {"fixed"} lines, each with its blank-line gap."""
    lines = text.split("\n")
    blocks: list[list[str]] = []
    gaps: list[int] = []
    gap = 0
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
            continue
        if current:
            blocks.append(current)
            gaps.append(gap)
            current, gap = [], 0
        gap += 1
    if current:
        blocks.append(current)
        gaps.append(gap)
    mode = "wrapped" if wrapped(blocks) else "lines"
    items: list[dict] = []
    for block, blank in zip(blocks, gaps, strict=True):
        if mode == "wrapped":
            value = " ".join(line.strip() for line in block)
            indent = block[0][: len(block[0]) - len(block[0].lstrip())]
            items.append({"gap": blank, "indent": indent, "value": value})
            continue
        for index, line in enumerate(block):
            stripped = line.strip()
            indent = line[: len(line) - len(line.lstrip())]
            items.append({"gap": blank if index == 0 else 0, "indent": indent, "value": stripped})
    return mode, items


def chapter_key(*parts) -> str:
    return hashlib.sha256(":".join(str(part) for part in parts).encode()).hexdigest()[:24]


def text_chapter(
    text: str,
    *,
    title: str,
    resource: str,
    first_line_title: bool = False,
    max_chars: int = MAX_UNIT_CHARS,
    max_length: int = 2_000_000,
) -> tuple[ImportedChapter, list[str]]:
    """Units, passages and layout of one chapter. `resource` must be stable for the same chapter."""
    text, removed = normalize(text)
    warnings = [f"{removed} caractère(s) de contrôle supprimé(s)."] if removed else []
    if len(text) > max_length:
        raise TextRejected(f"Chapitre trop long : {len(text)} caractères pour {max_length} autorisés.")
    if MARKER.search(text):
        raise TextRejected("Le texte contient des marqueurs réservés à Libris (⟦t0⟧…) : retirez-les.")
    mode, items = paragraphs(text)
    if not any(has_words(item["value"]) for item in items):
        raise TextRejected("Aucun texte traduisible dans ce chapitre.")
    first = next((index for index, item in enumerate(items) if has_words(item["value"])), None)
    # The first line names the chapter only if asked, or if it restates the number of the file name.
    heading_in_text = first is not None and (
        first_line_title or _restates_number(items[first]["value"], title)
    )
    units: list[ImportedUnit] = []
    layout: list[dict] = []
    section = 0

    def unit(index: str, value: str, tag: str, kind: str) -> ImportedUnit:
        return {
            "id": hashlib.sha256(f"{resource}:{index}".encode()).hexdigest()[:20],
            "text": value,
            "resource": resource,
            "path": index,
            "kind": kind,
            "attribute": "",
            "section": f"s{section}",
            "tag": tag,
        }

    chapter_title = title
    if heading_in_text:
        chapter_title = items[first]["value"][:500]
    elif has_words(title):
        # Translated with the chapter, used as its heading in consolidated exports, not written in the file.
        heading = unit("title", title, "h1", "title")
        units.append(heading)
        layout.append({"unit": heading["id"], "title": True, "emit": False})
    for index, item in enumerate(items):
        entry = {"gap": item["gap"], "indent": item["indent"]}
        if not has_words(item["value"]):
            # Scene breaks and ornaments stay as they are and start a new section.
            section += 1
            layout.append({**entry, "fixed": item["value"]})
            continue
        is_heading = heading_in_text and index == first
        value = unit(f"p{index}", item["value"], "h1" if is_heading else "p", "title" if is_heading else "paragraph")
        units.append(value)
        layout.append({**entry, "unit": value["id"], **({"title": True, "emit": True} if is_heading else {})})
    trailing = len(text) - len(text.rstrip("\n"))
    chapter = ImportedChapter(
        title=chapter_title[:500] or title[:500],
        resource=resource,
        groups=group_units(units, max_chars),
        checksum=hashlib.sha256(text.encode()).hexdigest(),
        meta={
            "layout": {"version": 1, "mode": mode, "items": layout, "trailing_newlines": trailing},
            # An archive restore cuts the text again: it must use the same passage size.
            "passage_max_chars": max_chars,
        },
    )
    return chapter, warnings


def _restates_number(line: str, title: str) -> bool:
    heading = CHAPTER_KEYWORD.match(line.strip())
    named = CHAPTER_KEYWORD.search(title)
    return bool(heading and named and float(heading.group(1).replace(",", ".")) == float(named.group(1).replace(",", ".")))


class TxtAdapter(SourceAdapter):
    format = "txt"
    media_type = "text/plain"

    def __init__(self, max_length: int = 2_000_000):
        self.max_length = max_length

    def inspect(self, name: str, data: bytes) -> FileInspection:
        found = FileInspection(name=name, format="txt", size=len(data), sha256=hashlib.sha256(data).hexdigest())
        guess = chapter_from_name(name)
        found.title = clean_title(name)
        found.chapter_number = guess.value
        found.number_confidence, found.number_reason = guess.confidence, guess.reason or "aucun numéro trouvé"
        try:
            text, encoding, warnings = decode(data)
            chapter, more = text_chapter(
                text, title=found.title, resource="txt/inspection", max_length=self.max_length
            )
        except TextRejected as exc:
            found.errors.append(str(exc))
            return found
        found.warnings += warnings + more
        units = [unit for group in chapter.groups for unit in group]
        found.meta = {
            "encoding": encoding,
            "checksum": chapter.checksum,
            "paragraphs": len({unit["path"] for unit in units if unit["path"] != "title"}),
            "characters": sum(len(unit["text"]) for unit in units),
            "layout": chapter.meta["layout"]["mode"],
            "first_line": next((u["text"][:200] for u in units if u["path"] != "title"), ""),
        }
        return found

    def parse(self, name: str, data: bytes, **options) -> ImportedChapter:
        text, encoding, warnings = decode(data)
        chapter, more = text_chapter(
            text,
            title=options.get("title") or clean_title(name),
            resource=options["resource"],
            first_line_title=bool(options.get("first_line_title")),
            max_chars=options.get("max_chars") or MAX_UNIT_CHARS,
            max_length=self.max_length,
        )
        chapter.number = options.get("number")
        chapter.meta.update(
            {"adapter": "txt", "version": 1, "original_name": name, "encoding": encoding, "warnings": warnings + more}
        )
        chapter.asset = ImportedAsset(name=name, format="txt", media_type="text/plain", data=data,
                                      meta={"encoding": encoding})
        return chapter
