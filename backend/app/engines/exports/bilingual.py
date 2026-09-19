"""Bilingual EPUB for proofreading on an e-reader: each source paragraph next to its translation.

The book is written from the volume's passages, not from the original file, so every volume gets
one whatever it was imported from (EPUB, TXT, Markdown, HTML, DOCX, JSON). Source and translation
are paired per original unit (a paragraph cut into several passages is joined again); markup codes,
images and inline styles are left out: the copy is made for reading the text, not for publishing.

Two layouts: `interleaved` writes the source paragraph, then its translation; `side-by-side` puts
them in two columns, which fall back to one under the other on a narrow screen. A passage without a
translation is shown with its source and an empty, marked translation.
"""

import io
import re
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Literal

from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.epub.text import plain
from app.engines.exports.text import HEADINGS, TEXT_KINDS, _paragraph, _units, chapter_segments, safe_filename
from app.languages import language_name, right_to_left
from app.models import Chapter, Project, Segment

Layout = Literal["interleaved", "side-by-side"]
LAYOUTS: tuple[str, ...] = ("interleaved", "side-by-side")
XHTML = "http://www.w3.org/1999/xhtml"
OPS = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")
# Characters XML 1.0 cannot carry (control characters of a pasted text).
NOT_XML = re.compile(r"[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")
LIST_MARKER = re.compile(r"^([-*+•]|\d+[.)])$")
MISSING = "—"

STYLE = """@charset "utf-8";
body { margin: 0 0.5em; line-height: 1.5; overflow-wrap: break-word; }
h1 { font-size: 1.35em; line-height: 1.25; margin: 1.2em 0 0.2em; }
p { margin: 0; text-indent: 0; orphans: 2; widows: 2; }
.title-page { text-align: center; margin-top: 3em; }
.title-page p { margin: 0.6em 0; }
.heading-source { font-size: 0.9em; font-style: italic; color: #555; margin: 0 0 1.4em; }
.pair { margin: 0 0 1em; }
.source { font-size: 0.92em; font-style: italic; color: #555; }
.interleaved .source { margin: 0 0 0.3em; padding-left: 0.6em; border-left: 2px solid #999; }
.side-by-side .pair { display: table; width: 100%; table-layout: fixed; }
.side-by-side .source, .side-by-side .translation { display: table-cell; width: 50%; vertical-align: top; }
.side-by-side .source { padding-right: 0.5em; }
.side-by-side .translation { padding-left: 0.5em; border-left: 1px solid #bbb; }
.subheading p { font-weight: bold; }
.quote { margin-left: 1em; }
.fixed { text-align: center; margin: 1em 0; white-space: pre-wrap; }
.missing { color: #888; }
@media (max-width: 30em) {
  .side-by-side .pair { display: block; }
  .side-by-side .source, .side-by-side .translation { display: block; width: auto; padding: 0; border: 0; }
  .side-by-side .source { margin: 0 0 0.3em; padding-left: 0.6em; border-left: 2px solid #999; }
}
"""


@dataclass
class Pair:
    source: str
    # None: the passage has no translation yet.
    translation: str | None
    kind: Literal["text", "subheading", "quote", "fixed"] = "text"
    # A list marker (`-`, `1.`) written before both texts.
    marker: str = ""


@dataclass
class BilingualChapter:
    chapter_id: str
    title: str
    translated_title: str | None
    pairs: list[Pair] = field(default_factory=list)
    missing_segments: int = 0

    @property
    def complete(self) -> bool:
        return self.missing_segments == 0


def _pending_units(segments: list[Segment]) -> tuple[set[str], int]:
    """Original units of the passages without a translation, and how many passages they are.
    A passage kept in its source on purpose is decided: its source is its translation."""
    keys: set[str] = set()
    count = 0
    for segment in segments:
        if not segment.translation and not segment.retained_source:
            count += 1
            keys.update(unit.get("original_id", unit["id"]) for unit in segment.units)
    return keys, count


def _epub_chapter(chapter: Chapter, segments: list[Segment], result: BilingualChapter) -> None:
    sources, _ = _units(segments, translated=False)
    targets, _ = _units(segments, translated=True)
    pending, _ = _pending_units(segments)
    units = [u for s in segments for u in s.units if u.get("kind") != "attribute" and u.get("tag") != "title"]
    tags = {unit.get("original_id", unit["id"]): unit.get("tag") for unit in units}
    wanted = " ".join(chapter.title.split()).casefold()
    titled = False
    for key in dict.fromkeys(tags):
        source = _paragraph(sources.get(key, ""))
        translation = None if key in pending else _paragraph(targets.get(key, ""))
        if not source and not translation:
            continue
        heading = tags[key] in HEADINGS
        if heading and not titled and source.casefold() == wanted:
            titled = True
            result.title, result.translated_title = source, translation
            continue
        result.pairs.append(Pair(source, translation, "subheading" if heading else "text"))


def _layout_chapter(chapter: Chapter, segments: list[Segment], result: BilingualChapter) -> None:
    sources, _ = _units(segments, translated=False)
    targets, _ = _units(segments, translated=True)
    pending, _ = _pending_units(segments)
    tags = {unit.get("original_id", unit["id"]): unit.get("tag") for s in segments for unit in s.units}
    for item in chapter.import_meta["layout"]["items"]:
        if "fixed" in item:
            result.pairs.append(Pair(item["fixed"], item["fixed"], "fixed"))
            continue
        key = item["unit"]
        source = plain(sources.get(key, ""))
        translation = None if key in pending else plain(targets.get(key, ""))
        if item.get("title"):
            result.title, result.translated_title = source.strip() or result.title, translation
            continue
        prefix = item.get("indent", "").strip()
        kind = "subheading" if tags.get(key) in HEADINGS or prefix.startswith("#") else "text"
        if prefix.startswith(">"):
            kind = "quote"
        marker = prefix if LIST_MARKER.match(prefix) else ""
        result.pairs.append(
            Pair(source.strip("\n"), None if translation is None else translation.strip("\n"), kind, marker)
        )


def chapter_pairs(chapter: Chapter, segments: list[Segment]) -> BilingualChapter:
    untitled = not (chapter.import_meta or {}).get("layout") and chapter.title == chapter.resource
    result = BilingualChapter(chapter.id, "" if untitled else chapter.title, None)
    if (chapter.import_meta or {}).get("layout"):
        _layout_chapter(chapter, segments, result)
    else:
        _epub_chapter(chapter, segments, result)
    result.missing_segments = _pending_units(segments)[1]
    return result


def volume_pairs(
    db: Session, project: Project, chapter_ids: set[str] | None = None
) -> list[BilingualChapter]:
    """The reading chapters of a volume in order, or only `chapter_ids` when given."""
    query = select(Chapter).where(Chapter.project_id == project.id, Chapter.kind.in_(TEXT_KINDS))
    if chapter_ids is not None:
        query = query.where(Chapter.id.in_(chapter_ids))
    segments = chapter_segments(db, project)
    return [
        chapter_pairs(chapter, segments.get(chapter.id, []))
        for chapter in db.scalars(query.order_by(Chapter.position))
    ]


def _clean(value: str) -> str:
    return NOT_XML.sub("", value or "")


def language_tag(code: str) -> str | None:
    value = (code or "").strip().replace("_", "-")
    return value if LANGUAGE_TAG.match(value) else None


def _with_lines(node: etree._Element, text: str) -> None:
    """Text with its line breaks kept as <br/> (poems, letters, hard-wrapped dialogue)."""
    parts = _clean(text).split("\n")
    node.text = parts[0]
    for part in parts[1:]:
        br = etree.SubElement(node, f"{{{XHTML}}}br")
        br.tail = part


def _lang(node: etree._Element, code: str) -> None:
    tag = language_tag(code)
    if tag:
        node.set("lang", tag)
        node.set(XML_LANG, tag)
    if right_to_left(code):
        node.set("dir", "rtl")


def _document(title: str, language: str, body_class: str = "") -> tuple[etree._Element, etree._Element]:
    html = etree.Element(f"{{{XHTML}}}html", nsmap={None: XHTML, "epub": OPS})
    _lang(html, language)
    head = etree.SubElement(html, f"{{{XHTML}}}head")
    etree.SubElement(head, f"{{{XHTML}}}meta", charset="utf-8")
    etree.SubElement(head, f"{{{XHTML}}}title").text = _clean(title) or "—"
    etree.SubElement(head, f"{{{XHTML}}}meta", name="viewport", content="width=device-width, initial-scale=1")
    etree.SubElement(head, f"{{{XHTML}}}link", rel="stylesheet", type="text/css", href="style.css")
    body = etree.SubElement(html, f"{{{XHTML}}}body")
    if body_class:
        body.set("class", body_class)
    return html, body


def _serialize(html: etree._Element) -> bytes:
    return b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n' + etree.tostring(
        html, encoding="utf-8"
    )


def _paragraph_node(parent: etree._Element, role: str, text: str | None, language: str, marker: str) -> None:
    node = etree.SubElement(parent, f"{{{XHTML}}}p")
    if text is None:
        node.set("class", f"{role} missing")
        node.text = MISSING
        return
    node.set("class", role)
    _lang(node, language)
    _with_lines(node, (marker + " " if marker else "") + text)


def _chapter_page(item: BilingualChapter, label: str, layout: str, source: str, target: str) -> bytes:
    html, body = _document(label, target, layout)
    section = etree.SubElement(body, f"{{{XHTML}}}section")
    section.set(f"{{{OPS}}}type", "chapter")
    section.set("role", "doc-chapter")
    h1 = etree.SubElement(section, f"{{{XHTML}}}h1")
    _with_lines(h1, label)
    if item.title and item.translated_title and item.title.strip() != item.translated_title.strip():
        subtitle = etree.SubElement(section, f"{{{XHTML}}}p", {"class": "heading-source"})
        _lang(subtitle, source)
        _with_lines(subtitle, item.title)
    for pair in item.pairs:
        if pair.kind == "fixed":
            etree.SubElement(section, f"{{{XHTML}}}p", {"class": "fixed"}).text = _clean(pair.source)
            continue
        block = etree.SubElement(section, f"{{{XHTML}}}div")
        block.set("class", "pair" + {"subheading": " subheading", "quote": " quote"}.get(pair.kind, ""))
        _paragraph_node(block, "source", pair.source, source, pair.marker)
        _paragraph_node(block, "translation", pair.translation, target, pair.marker)
    return _serialize(html)


def build_bilingual_epub(
    project: Project,
    chapters: list[BilingualChapter],
    layout: str = "interleaved",
    modified: float | None = None,
) -> bytes:
    """A valid EPUB 3: a title page, one XHTML file per chapter, a navigation document."""
    if layout not in LAYOUTS:
        raise ValueError(f"Disposition bilingue inconnue : {layout}.")
    source, target = project.source_language, project.target_language
    title = _clean(project.title) or "Libris"
    files: list[tuple[str, str, bytes]] = []  # (href, label, content)

    html, body = _document(title, target)
    page = etree.SubElement(body, f"{{{XHTML}}}section", {"class": "title-page"})
    page.set(f"{{{OPS}}}type", "titlepage")
    etree.SubElement(page, f"{{{XHTML}}}h1").text = title
    if project.author:
        etree.SubElement(page, f"{{{XHTML}}}p").text = _clean(project.author)
    languages = etree.SubElement(page, f"{{{XHTML}}}p")
    languages.text = _clean(f"{language_name(source)} → {language_name(target)}")
    files.append(("title.xhtml", title, _serialize(html)))

    for index, item in enumerate(chapters, 1):
        if not item.pairs and not item.title and not item.translated_title:
            continue
        label = (item.translated_title or item.title or "").strip() or str(index)
        files.append(
            (f"chapter-{index:04d}.xhtml", label, _chapter_page(item, label, layout, source, target))
        )

    html, body = _document(title, target)
    nav = etree.SubElement(body, f"{{{XHTML}}}nav", id="toc")
    nav.set(f"{{{OPS}}}type", "toc")
    etree.SubElement(nav, f"{{{XHTML}}}h1").text = title
    entries = etree.SubElement(nav, f"{{{XHTML}}}ol")
    for href, label, _ in files[1:] or files:
        link = etree.SubElement(etree.SubElement(entries, f"{{{XHTML}}}li"), f"{{{XHTML}}}a", href=href)
        link.text = " ".join(_clean(label).split()) or "—"
    nav_page = _serialize(html)

    package = etree.Element(
        f"{{{OPF}}}package", nsmap={None: OPF}, version="3.0", attrib={"unique-identifier": "uid"}
    )
    metadata = etree.SubElement(package, f"{{{OPF}}}metadata", nsmap={"dc": DC})
    identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"libris:bilingual:{project.id}")
    etree.SubElement(metadata, f"{{{DC}}}identifier", id="uid").text = f"urn:uuid:{identifier}"
    etree.SubElement(metadata, f"{{{DC}}}title").text = title
    for code in dict.fromkeys(filter(None, (language_tag(target) or "und", language_tag(source)))):
        etree.SubElement(metadata, f"{{{DC}}}language").text = code
    if project.author:
        etree.SubElement(metadata, f"{{{DC}}}creator").text = _clean(project.author)
    stamp = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(modified if modified is not None else time.time())
    )
    etree.SubElement(metadata, f"{{{OPF}}}meta", property="dcterms:modified").text = stamp
    manifest = etree.SubElement(package, f"{{{OPF}}}manifest")
    etree.SubElement(
        manifest,
        f"{{{OPF}}}item",
        id="nav",
        href="nav.xhtml",
        attrib={"media-type": "application/xhtml+xml", "properties": "nav"},
    )
    etree.SubElement(
        manifest, f"{{{OPF}}}item", id="style", href="style.css", attrib={"media-type": "text/css"}
    )
    spine = etree.SubElement(package, f"{{{OPF}}}spine")
    if right_to_left(target):
        spine.set("page-progression-direction", "rtl")
    for number, (href, _, _) in enumerate(files):
        item_id = f"page-{number}"
        etree.SubElement(
            manifest, f"{{{OPF}}}item", id=item_id, href=href, attrib={"media-type": "application/xhtml+xml"}
        )
        etree.SubElement(spine, f"{{{OPF}}}itemref", idref=item_id)

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/content.opf",
            etree.tostring(package, encoding="utf-8", xml_declaration=True),
            compress_type=zipfile.ZIP_DEFLATED,
        )
        archive.writestr("OEBPS/nav.xhtml", nav_page, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/style.css", STYLE, compress_type=zipfile.ZIP_DEFLATED)
        for href, _, content in files:
            archive.writestr(f"OEBPS/{href}", content, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()


def bilingual_filename(title: str) -> str:
    return safe_filename(title) + " - bilingue.epub"
