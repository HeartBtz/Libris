"""Structured documents (Markdown, HTML, DOCX): one file is one chapter, like a TXT file.

Each adapter only reads its format into `Block`s (a heading, a paragraph, a list item, or a fixed
block such as code or a scene break). The blocks become the same units, passages and recorded layout
as a TXT chapter, so analysis, translation, review and the text exports treat them alike. Block
markup that the text exports should give back (`## `, `- `, `> `) travels in the layout's `indent`,
never in the translated text.
"""

import hashlib
from abc import abstractmethod
from dataclasses import dataclass, field

from app.engines.epub.text import MARKER, group_units
from app.engines.ingestion.base import (
    FileInspection,
    ImportedAsset,
    ImportedChapter,
    ImportedUnit,
    SourceAdapter,
)
from app.engines.ingestion.naming import chapter_from_name, clean_title
from app.engines.ingestion.passages import DEFAULT_PASSAGE_CHARS
from app.engines.ingestion.text import FORBIDDEN, TextRejected, has_words

HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class Block:
    text: str
    tag: str = "p"
    # Markup written back before the text by the text exports (Markdown heading or list marker…).
    prefix: str = ""
    # Code, tables of symbols, rules: kept exactly, never sent to the model.
    fixed: bool = False
    gap: int = 1


@dataclass
class Document:
    blocks: list[Block]
    title: str = ""
    author: str = ""
    language: str = ""
    warnings: list[str] = field(default_factory=list)


def document_chapter(
    document: Document,
    *,
    title: str,
    resource: str,
    max_chars: int = DEFAULT_PASSAGE_CHARS,
    max_length: int = 2_000_000,
) -> tuple[ImportedChapter, list[str]]:
    """Units, passages and layout of one document. `resource` must be stable for the same chapter."""
    warnings = list(document.warnings)
    removed = 0
    blocks = []
    for block in document.blocks:
        text, count = FORBIDDEN.subn("", block.text)
        removed += count
        if text.strip():
            blocks.append(
                Block(
                    text if block.fixed else " ".join(text.split()),
                    block.tag,
                    block.prefix,
                    block.fixed,
                    block.gap,
                )
            )
    if removed:
        warnings.append(f"{removed} caractère(s) de contrôle supprimé(s).")
    length = sum(len(block.text) for block in blocks)
    if length > max_length:
        raise TextRejected(f"Chapitre trop long : {length} caractères pour {max_length} autorisés.")
    if any(MARKER.search(block.text) for block in blocks):
        raise TextRejected("Le texte contient des marqueurs réservés à Libris (⟦t0⟧…) : retirez-les.")
    readable = [index for index, block in enumerate(blocks) if not block.fixed and has_words(block.text)]
    if not readable:
        raise TextRejected("Aucun texte traduisible dans ce chapitre.")
    first = readable[0]
    # A document opening on a heading names its chapter with it; otherwise the file's title is used.
    heading_in_text = blocks[first].tag in HEADINGS
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

    chapter_title = blocks[first].text if heading_in_text else title
    if not heading_in_text and has_words(chapter_title):
        heading = unit("title", chapter_title[:500], "h1", "title")
        units.append(heading)
        layout.append({"unit": heading["id"], "title": True, "emit": False})
    for index, block in enumerate(blocks):
        entry = {"gap": block.gap if layout else 0, "indent": block.prefix}
        if block.fixed or not has_words(block.text):
            section += 1
            layout.append({**entry, "fixed": block.text})
            continue
        is_heading = heading_in_text and index == first
        value = unit(f"b{index}", block.text, block.tag, "title" if block.tag in HEADINGS else "paragraph")
        units.append(value)
        layout.append({**entry, "unit": value["id"], **({"title": True, "emit": True} if is_heading else {})})
    checksum = hashlib.sha256(
        "\n".join(f"{block.tag}|{block.prefix}|{block.fixed}|{block.text}" for block in blocks).encode()
    ).hexdigest()
    chapter = ImportedChapter(
        title=chapter_title[:500] or title[:500],
        resource=resource,
        groups=group_units(units, max_chars),
        checksum=checksum,
        meta={
            "layout": {"version": 1, "mode": "document", "items": layout, "trailing_newlines": 1},
            "passage_max_chars": max_chars,
        },
    )
    return chapter, warnings


class DocumentAdapter(SourceAdapter):
    """Shared inspection and parsing; subclasses only implement `read`."""

    format: str
    media_type: str

    def __init__(self, max_length: int = 2_000_000):
        self.max_length = max_length

    @abstractmethod
    def read(self, data: bytes) -> Document:
        """The document's blocks and metadata; raises TextRejected on an unreadable file."""

    def inspect(self, name: str, data: bytes) -> FileInspection:
        found = FileInspection(
            name=name, format=self.format, size=len(data), sha256=hashlib.sha256(data).hexdigest()
        )
        guess = chapter_from_name(name)
        found.chapter_number = guess.value
        found.number_confidence, found.number_reason = guess.confidence, guess.reason or "aucun numéro trouvé"
        try:
            document = self.read(data)
            found.title = (document.title or clean_title(name))[:500]
            chapter, warnings = document_chapter(
                document, title=found.title, resource=f"{self.format}/inspection", max_length=self.max_length
            )
        except TextRejected as exc:
            found.title = found.title or clean_title(name)
            found.errors.append(str(exc))
            return found
        found.warnings += warnings
        found.author, found.language = document.author[:500], document.language[:80]
        units = [unit for group in chapter.groups for unit in group]
        found.meta = {
            "checksum": chapter.checksum,
            "paragraphs": sum(1 for unit in units if unit["path"] != "title"),
            "characters": sum(len(unit["text"]) for unit in units),
            "layout": "document",
            "first_line": next((u["text"][:200] for u in units if u["path"] != "title"), ""),
        }
        return found

    def parse(self, name: str, data: bytes, **options) -> ImportedChapter:
        document = self.read(data)
        chapter, warnings = document_chapter(
            document,
            title=options.get("title") or document.title or clean_title(name),
            resource=options["resource"],
            max_chars=options.get("max_chars") or DEFAULT_PASSAGE_CHARS,
            max_length=self.max_length,
        )
        chapter.number = options.get("number")
        chapter.meta.update(
            {"adapter": self.format, "version": 1, "original_name": name, "warnings": warnings}
        )
        chapter.asset = ImportedAsset(name=name, format=self.format, media_type=self.media_type, data=data)
        return chapter


def decode_text(data: bytes) -> tuple[str, list[str]]:
    """Markdown and HTML files: the TXT decoding rules (UTF-8, UTF-16 with BOM, flagged Windows-1252)."""
    from app.engines.ingestion.text import decode

    text, _, warnings = decode(data)
    return text.replace("\r\n", "\n").replace("\r", "\n"), warnings
