"""Word documents (DOCX) read with the ZIP and XML checks of the EPUB import, without python-docx.

Paragraphs are read in document order, those of tables and text boxes included; headings come from
the paragraph style (`Title`, `heading 1`…, or an outline level) and list paragraphs keep a list
marker in the text exports. Deleted revisions, field codes and the fallback copy of drawings
(`mc:Fallback`) are not text. Formatting (bold, italics) is not kept: the export is plain text.
"""

import re
import zipfile

from lxml import etree

from app.engines.epub.archive import unpack_archive, xml
from app.engines.ingestion.document import Block, Document, DocumentAdapter
from app.engines.ingestion.text import TextRejected

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {
    "w": W,
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
}
HEADING_NAME = re.compile(
    r"^(?:heading|titre|überschrift|titolo|encabezado|título)\s*([1-6])$", re.IGNORECASE
)


def _q(name: str) -> str:
    return f"{{{W}}}{name}"


def style_levels(styles: bytes | None) -> dict[str, int]:
    """Heading level of each paragraph style id, from its name or its outline level."""
    if not styles:
        return {}
    levels = {}
    for style in xml(styles).iter(_q("style")):
        identifier = style.get(_q("styleId"), "")
        name_node = style.find(_q("name"))
        name = (name_node.get(_q("val"), "") if name_node is not None else "").strip()
        outline = style.find(f"{_q('pPr')}/{_q('outlineLvl')}")
        if name.casefold() == "title":
            levels[identifier] = 1
        elif match := HEADING_NAME.match(name):
            levels[identifier] = int(match[1])
        elif (
            outline is not None
            and (outline.get(_q("val")) or "").isdigit()
            and int(outline.get(_q("val"))) < 6
        ):
            levels[identifier] = int(outline.get(_q("val"))) + 1
    return levels


def _paragraph_text(paragraph) -> str:
    parts = []
    for node in paragraph.iter(_q("t"), _q("tab"), _q("br"), _q("cr"), _q("noBreakHyphen")):
        tag = etree.QName(node).localname
        # A nested paragraph (text box) is read on its own.
        owner = next((a for a in node.iterancestors(_q("p"))), None)
        if owner is not paragraph:
            continue
        parts.append(
            node.text or ""
            if tag == "t"
            else "\t"
            if tag == "tab"
            else "-"
            if tag == "noBreakHyphen"
            else " "
        )
    return "".join(parts)


def _level(paragraph, levels: dict[str, int]) -> int | None:
    properties = paragraph.find(_q("pPr"))
    if properties is None:
        return None
    outline = properties.find(_q("outlineLvl"))
    if outline is not None and (outline.get(_q("val")) or "").isdigit() and int(outline.get(_q("val"))) < 6:
        return int(outline.get(_q("val"))) + 1
    style = properties.find(_q("pStyle"))
    if style is not None:
        identifier = style.get(_q("val"), "")
        if identifier in levels:
            return levels[identifier]
        # Documents written without a styles part still name their styles "Heading1", "Title"…
        if identifier.casefold() == "title":
            return 1
        if match := re.match(r"^heading([1-6])$", identifier, re.IGNORECASE):
            return int(match[1])
    return None


def read_docx(data: bytes) -> Document:
    try:
        entries = unpack_archive(data)
    except (ValueError, zipfile.BadZipFile) as exc:
        raise TextRejected(f"Document Word illisible : {str(exc)[:300] or 'archive ZIP invalide'}") from None
    if "word/document.xml" not in entries or "[Content_Types].xml" not in entries:
        raise TextRejected("Ce fichier n’est pas un document Word (DOCX).")
    try:
        root = xml(entries["word/document.xml"])
        levels = style_levels(entries.get("word/styles.xml"))
        core = xml(entries["docProps/core.xml"]) if "docProps/core.xml" in entries else None
    except (ValueError, etree.XMLSyntaxError) as exc:
        raise TextRejected(f"Document Word illisible : {str(exc)[:300]}") from None
    body = root.find(_q("body"))
    if body is None:
        raise TextRejected("Document Word sans contenu.")
    blocks: list[Block] = []
    for paragraph in body.iter(_q("p")):
        if any(
            etree.QName(a).namespace == MC and etree.QName(a).localname == "Fallback"
            for a in paragraph.iterancestors()
        ):
            continue
        text = _paragraph_text(paragraph)
        if not text.strip():
            continue
        level = _level(paragraph, levels)
        listed = paragraph.find(f"{_q('pPr')}/{_q('numPr')}") is not None
        if level:
            blocks.append(Block(text, f"h{level}", "#" * level + " "))
        elif listed:
            blocks.append(Block(text, "li", "- ", gap=0 if blocks and blocks[-1].tag == "li" else 1))
        else:
            blocks.append(Block(text))
    if blocks:
        blocks[0].gap = 0

    def core_value(path: str) -> str:
        return " ".join((core.findtext(path, default="", namespaces=NS) if core is not None else "").split())

    return Document(
        blocks,
        title=core_value("dc:title")[:500],
        author=core_value("dc:creator")[:500],
        language=core_value("dc:language")[:80],
    )


class DocxAdapter(DocumentAdapter):
    format = "docx"
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def read(self, data: bytes) -> Document:
        return read_docx(data)
