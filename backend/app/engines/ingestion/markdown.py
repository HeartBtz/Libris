"""Markdown chapters (CommonMark subset, no dependency).

Block structure is read (headings, paragraphs, list items, block quotes, fenced code, rules, tables,
YAML front matter); inline markup (`*emphasis*`, `[links](…)`) stays in the text the model receives
and gives back, as the translation prompt keeps markup it does not understand. Fenced code, tables
and HTML comments are kept untranslated; soft line breaks inside a paragraph become spaces.
"""

import re

from app.engines.ingestion.document import Block, Document, DocumentAdapter, decode_text

ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
ITEM = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
QUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
TABLE = re.compile(r"^ {0,3}\|")
COMMENT = re.compile(r"^ {0,3}<!--")
META = re.compile(r"^(title|author|lang|language)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def front_matter(lines: list[str]) -> tuple[dict, int]:
    """YAML front matter's simple `key: value` lines; the index of the first line after it."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for end in range(1, min(len(lines), 200)):
        if lines[end].strip() in ("---", "..."):
            found = {}
            for line in lines[1:end]:
                if match := META.match(line):
                    found[match[1].casefold()] = match[2].strip().strip("\"'")
            return found, end + 1
    return {}, 0


def read_markdown(text: str) -> Document:
    lines = text.split("\n")
    meta, index = front_matter(lines)
    blocks: list[Block] = []
    gap = 0

    start = index

    def add(block: Block) -> None:
        nonlocal gap
        block.gap = gap if blocks else 0
        block.line = start
        blocks.append(block)
        gap = 0

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            gap += 1
            index += 1
            continue
        start = index
        if fence := FENCE.match(line):
            closing = next(
                (end for end in range(index + 1, len(lines)) if lines[end].strip().startswith(fence[1])),
                len(lines) - 1,
            )
            add(Block("\n".join(lines[index : closing + 1]), "pre", fixed=True))
            index = closing + 1
            continue
        if COMMENT.match(line):
            closing = next((end for end in range(index, len(lines)) if "-->" in lines[end]), len(lines) - 1)
            add(Block("\n".join(lines[index : closing + 1]), "comment", fixed=True))
            index = closing + 1
            continue
        if heading := ATX.match(line):
            level = len(heading[1])
            add(Block(heading[2] or "", f"h{level}", "#" * level + " ", fixed=not (heading[2] or "").strip()))
            index += 1
            continue
        if RULE.match(line):
            add(Block(line.strip(), "hr", fixed=True))
            index += 1
            continue
        if TABLE.match(line):
            end = index
            while end < len(lines) and TABLE.match(lines[end]):
                end += 1
            add(Block("\n".join(lines[index:end]), "table", fixed=True))
            index = end
            continue
        if quote := QUOTE.match(line):
            parts = [quote[1]]
            index += 1
            while index < len(lines) and (match := QUOTE.match(lines[index])) and match[1].strip():
                parts.append(match[1])
                index += 1
            add(Block(" ".join(parts), "blockquote", "> "))
            continue
        if item := ITEM.match(line):
            parts = [item[3]]
            index += 1
            # Lazy continuation: indented lines that start nothing new belong to the item.
            while (
                index < len(lines)
                and lines[index].startswith((" ", "\t"))
                and lines[index].strip()
                and not (ITEM.match(lines[index]) or FENCE.match(lines[index]))
            ):
                parts.append(lines[index].strip())
                index += 1
            add(Block(" ".join(parts), "li", f"{item[1]}{item[2]} "))
            continue
        parts = [line.strip()]
        index += 1
        tag = "p"
        while index < len(lines) and lines[index].strip():
            following = lines[index]
            if SETEXT.match(following):
                tag = "h1" if following.strip().startswith("=") else "h2"
                index += 1
                break
            if (
                ATX.match(following)
                or FENCE.match(following)
                or QUOTE.match(following)
                or RULE.match(following)
            ):
                break
            if ITEM.match(following) and not following.startswith((" ", "\t")):
                break
            parts.append(following.strip())
            index += 1
        prefix = {"h1": "# ", "h2": "## "}.get(tag, "")
        add(Block(" ".join(parts), tag, prefix))
    return Document(
        blocks,
        title=meta.get("title", "")[:500],
        author=meta.get("author", "")[:500],
        language=(meta.get("lang") or meta.get("language") or "")[:80],
    )


class MarkdownAdapter(DocumentAdapter):
    format = "md"
    media_type = "text/markdown"

    def read(self, data: bytes) -> Document:
        text, warnings = decode_text(data)
        document = read_markdown(text)
        document.warnings = warnings + document.warnings
        if any(block.tag == "table" for block in document.blocks):
            document.warnings.append("Les tableaux Markdown sont conservés tels quels, sans traduction.")
        return document
