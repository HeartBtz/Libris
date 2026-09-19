"""HTML chapters: the readable blocks of one page, without scripts, styles or network access.

Headings, paragraphs, list items, block quotes and table cells become units; `<pre>` and `<hr>` are
kept untranslated. Inline formatting is flattened to its text (the text exports have no markup to
put it back into); `<br>` becomes a space like a soft break in Markdown.
"""

from lxml import etree, html

from app.engines.ingestion.document import Block, Document, DocumentAdapter, decode_text
from app.engines.ingestion.text import TextRejected

SKIPPED = {
    "script",
    "style",
    "template",
    "noscript",
    "head",
    "title",
    "svg",
    "math",
    "iframe",
    "object",
    "canvas",
    "nav",
    "button",
    "select",
    "textarea",
}
LEAVES = {
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "dt",
    "dd",
    "td",
    "th",
    "caption",
    "figcaption",
    "summary",
}
CONTAINERS = {
    "body",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "aside",
    "blockquote",
    "ul",
    "ol",
    "dl",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "figure",
    "details",
    "center",
    "form",
    "fieldset",
    "hgroup",
    "address",
}
BLOCKS = LEAVES | CONTAINERS | {"pre", "hr"}
PREFIXES = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### ", "li": "- "}


def _tag(node) -> str:
    return node.tag.rsplit("}", 1)[-1].casefold() if isinstance(node.tag, str) else ""


def _has_block(node) -> bool:
    return any(_tag(child) in BLOCKS or _has_block(child) for child in node if _tag(child) not in SKIPPED)


def _text(node) -> str:
    """Text of an inline run, skipped elements left out."""
    parts = [node.text or ""]
    for child in node:
        if _tag(child) not in SKIPPED:
            parts.append(_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _walk(node, blocks: list[Block], quoted: bool = False) -> None:
    tag = _tag(node)
    if tag in SKIPPED or not tag:
        return
    if tag == "pre":
        blocks.append(Block(_text(node).strip("\n"), "pre", fixed=True))
        return
    if tag == "hr":
        blocks.append(Block("* * *", "hr", fixed=True))
        return
    inside = quoted or tag == "blockquote"
    if not _has_block(node):
        blocks.append(
            Block(
                _text(node),
                "blockquote" if inside and tag not in PREFIXES else tag if tag in LEAVES else "p",
                ("> " if inside else "") + PREFIXES.get(tag, ""),
                gap=0 if tag in {"li", "td", "th", "dd"} and blocks and blocks[-1].tag == tag else 1,
            )
        )
        return
    # Loose text between blocks (`<div>text<p>…</p>more</div>`) is a paragraph of its own.
    run = [node.text or ""]
    for child in node:
        if _tag(child) in BLOCKS or _has_block(child):
            blocks.append(Block("".join(run), "blockquote" if inside else "p", "> " if inside else ""))
            _walk(child, blocks, inside)
            run = [child.tail or ""]
        else:
            run += [_text(child) if _tag(child) not in SKIPPED else "", child.tail or ""]
    blocks.append(Block("".join(run), "blockquote" if inside else "p", "> " if inside else ""))


def read_html(text: str) -> Document:
    if "<!entity" in text.casefold():
        raise TextRejected("Les déclarations d’entités ne sont pas autorisées dans un fichier HTML.")
    parser = html.HTMLParser(no_network=True, remove_comments=True, remove_pis=True, recover=True)
    try:
        root = html.document_fromstring(text, parser=parser)
    except (etree.ParserError, ValueError):
        raise TextRejected("Fichier HTML illisible.") from None
    for br in root.iter("br"):
        br.tail = " " + (br.tail or "")
    body = root.find("body")
    blocks: list[Block] = []
    _walk(body if body is not None else root, blocks)
    blocks = [block for block in blocks if block.text.strip()]
    if blocks:
        blocks[0].gap = 0
    title = " ".join((root.findtext(".//title") or "").split())
    author = next(
        (m.get("content", "") for m in root.iter("meta") if (m.get("name") or "").casefold() == "author"), ""
    )
    return Document(
        blocks,
        title=title[:500],
        author=" ".join(author.split())[:500],
        language=(root.get("lang") or "")[:80],
    )


class HtmlAdapter(DocumentAdapter):
    format = "html"
    media_type = "text/html"

    def read(self, data: bytes) -> Document:
        text, warnings = decode_text(data)
        document = read_html(text)
        document.warnings = warnings + document.warnings
        return document
