"""One TXT, Markdown or DOCX file holding many chapters, split at its chapter headings.

Webnovels often come as one big file. Detection reads the file's lines (TXT), blocks (Markdown, with
the line each one starts on) or paragraphs (DOCX) and proposes where chapters start:

1. Heading styles first (Word `Heading 1`/`Titre 1`…, Markdown `#` headings): the highest level used
   at least twice.
2. Otherwise heading lines ("Chapter 12", "Chapitre 12 : Titre", "CHAPTER XII", "第12章", "Prologue"…,
   read by `naming.chapter_heading`), standing on their own line.
3. Otherwise numbered lines ("12. Title") only when they follow each other (1, 2, 3…).

A heading must be followed by text before the next one: a run of headings with nothing between them
is a table of contents and only its last line may start a chapter. Text before the first heading
becomes a front matter chapter when it has words. Numbers come from the headings, otherwise they are
given in order between their neighbours; out-of-order and repeated numbers leave the heading in the
text of the chapter before, with a warning.

Each part becomes the file it would have been if uploaded alone (`part_file`): the same adapter reads
it, so its passages, layout and checksum are those of a separate upload, and a project archive cuts
it again the same way.
"""

import io
import re
import zipfile
from bisect import bisect_left
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from app.engines.ingestion import chapter_adapter
from app.engines.ingestion.document import HEADINGS, Block, decode_text
from app.engines.ingestion.docx import read_docx
from app.engines.ingestion.markdown import read_markdown
from app.engines.ingestion.naming import HIGH, MEDIUM, chapter_heading, missing_numbers, stem
from app.engines.ingestion.text import TextRejected, decode, has_words, normalize, paragraphs

SPLIT_FORMATS = ("txt", "md", "docx")
FRONT_MATTER = "Avant-propos"
EXCERPT = 160
SENTENCE_END = (".", "!", "?", "…", ",", ";", ":", '"', "»", "”", "’")
MAX_PARTS = 5000
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass
class Row:
    """A place a chapter may start on: a line (TXT, Markdown) or a paragraph (DOCX)."""

    index: int
    text: str
    level: int | None = None
    # TXT: the line may be a heading with a title (not inside a hard-wrapped paragraph).
    alone: bool = True


@dataclass
class Source:
    """A file read for splitting: its rows and what each part of it is made of."""

    fmt: str
    name: str
    rows: list[Row]
    lines: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    encoding: str = "utf-8"
    warnings: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.blocks) if self.fmt == "docx" else len(self.lines)

    def starts(self) -> set[int]:
        """Every place a boundary may be put: the start of a row with words."""
        return {row.index for row in self.rows if has_words(row.text)}


@dataclass
class Part:
    start: int
    number: float | None
    title: str
    heading: bool
    # Rows of the heading (the heading line, and a subtitle line joined to its title).
    heading_rows: int = 0
    from_heading: bool = False
    characters: int = 0
    first_line: str = ""
    excerpt: str = ""
    checksum: str = ""


@dataclass
class Split:
    parts: list[Part]
    confidence: str
    reason: str
    warnings: list[str] = field(default_factory=list)


def read_source(fmt: str, name: str, data: bytes) -> Source:
    """The rows of a TXT, Markdown or DOCX file; raises TextRejected like the adapters."""
    if fmt == "txt":
        text, encoding, warnings = decode(data)
        text, _ = normalize(text)
        lines = text.split("\n")
        # Hard-wrapped prose: a line may start with "Chapter 3. Then…" in the middle of a paragraph.
        wrapped = paragraphs(text)[0] == "wrapped"
        rows = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            before = index == 0 or not lines[index - 1].strip()
            after = index == len(lines) - 1 or not lines[index + 1].strip()
            rows.append(Row(index, line.strip(), alone=before or after or not wrapped))
        return Source(fmt, name, rows, lines=lines, encoding=encoding, warnings=warnings)
    if fmt == "md":
        text, warnings = decode_text(data)
        document = read_markdown(text)
        rows = [
            Row(
                block.line,
                block.text,
                int(block.tag[1]) if block.tag in HEADINGS and not block.fixed else None,
            )
            for block in document.blocks
            if not block.fixed and block.line >= 0
        ]
        return Source(fmt, name, rows, lines=text.split("\n"), warnings=warnings)
    if fmt == "docx":
        document = read_docx(data)
        rows = [
            Row(index, block.text, int(block.tag[1]) if block.tag in HEADINGS else None)
            for index, block in enumerate(document.blocks)
        ]
        return Source(fmt, name, rows, blocks=document.blocks)
    raise TextRejected(f"Format {fmt} non découpable.")


def _with_body(rows: list[Row], chosen: list[int]) -> list[int]:
    """Headings followed by text before the next heading: a run of headings with nothing between them
    (a table of contents) keeps only its last line."""
    kept = []
    positions = set(chosen)
    for order, position in enumerate(chosen):
        end = chosen[order + 1] if order + 1 < len(chosen) else len(rows)
        body = any(has_words(rows[p].text) for p in range(position + 1, end) if p not in positions)
        if body:
            kept.append(position)
    return kept


def _subtitle(rows: list[Row], position: int, headings: set[int]) -> str:
    """A short line right under a bare heading ("Chapter 1" / "The Beginning") completes its title,
    unless the text goes on right after it (it is then the first paragraph)."""
    following = position + 1
    if following >= len(rows) or following in headings:
        return ""
    row = rows[following]
    line = row.text.strip()
    if len(line) > 80 or line.endswith(SENTENCE_END) or row.level:
        return ""
    after = following + 1
    if after < len(rows) and rows[after].index == row.index + 1:
        return ""
    return line


def _increasing(numbers: list[tuple[int, float]]) -> set[int]:
    """Positions of the longest run of strictly increasing numbers (a table of contents or a stray
    "Chapter 3" does not break the sequence of the real headings)."""
    tails: list[int] = []
    previous: dict[int, int | None] = {}
    for order, (_, value) in enumerate(numbers):
        low, high = 0, len(tails)
        while low < high:
            middle = (low + high) // 2
            if numbers[tails[middle]][1] < value:
                low = middle + 1
            else:
                high = middle
        previous[order] = tails[low - 1] if low else None
        if low == len(tails):
            tails.append(order)
        else:
            tails[low] = order
    kept, current = set(), tails[-1] if tails else None
    while current is not None:
        kept.add(numbers[current][0])
        current = previous[current]
    return kept


def detect(source: Source) -> Split | None:
    """The proposed split of a file, or None when it does not hold at least two chapter headings."""
    rows = source.rows
    found = _styled(rows) or _keywords(rows) or _numbered(rows)
    if not found:
        return None
    chosen, confidence, reason = found
    warnings: list[str] = []
    headings = [(position, chapter_heading(rows[position].text)) for position in chosen]
    # A number that goes back (or repeats) is a sentence or a typo: it stays in the chapter before.
    ordered = _increasing([(p, h.number) for p, h in headings if h and h.number is not None])
    kept = []
    for position, heading in headings:
        if heading and heading.number is not None and position not in ordered:
            # The last line of a table of contents is dropped silently.
            previous = rows[position - 1] if position else None
            if previous and (previous.level or chapter_heading(previous.text)):
                continue
            warnings.append(
                f"Le titre « {rows[position].text[:80]} » ne suit pas l’ordre des chapitres : "
                "il reste dans le chapitre précédent."
            )
            continue
        kept.append((position, heading))
    if len(kept) < 2:
        return None
    if warnings and confidence == HIGH:
        confidence = MEDIUM
    positions = {position for position, _ in kept}
    parts: list[Part] = []
    first = kept[0][0]
    if any(has_words(row.text) for row in rows[:first]):
        parts.append(Part(0, None, "", heading=False))
    for order, (position, heading) in enumerate(kept):
        title = rows[position].text.strip()
        heading_rows = 1
        subtitle = "" if (heading and heading.subtitle) else _subtitle(rows, position, positions)
        if subtitle and source.fmt == "txt":
            title, heading_rows = f"{title} — {subtitle}", 2
        start = 0 if order == 0 and not parts else rows[position].index
        parts.append(
            Part(start, heading.number if heading else None, title[:500], heading=True, heading_rows=heading_rows,
                 from_heading=bool(heading and heading.number is not None))
        )  # fmt: skip
        parts[-1].first_line = rows[position].text[:200]
    numbers = [part.number for part in parts if part.number is not None]
    whole = [int(n) for n in numbers if float(n).is_integer()]
    gaps = missing_numbers(whole, start=min(whole)) if whole else []
    if gaps:
        shown = ", ".join(str(n) for n in gaps[:20]) + (" …" if len(gaps) > 20 else "")
        warnings.append(f"Chapitres absents du fichier : {shown}.")
        if confidence == HIGH:
            confidence = MEDIUM
    fill_numbers(parts)
    if len(parts) > MAX_PARTS:
        return None
    describe(source, parts)
    return Split(parts, confidence, reason, warnings)


def _styled(rows: list[Row]) -> tuple[list[int], str, str] | None:
    for level in (1, 2, 3):
        chosen = _with_body(rows, [p for p, row in enumerate(rows) if row.level == level])
        if len(chosen) >= 2:
            named = sum(1 for p in chosen if chapter_heading(rows[p].text))
            confidence = HIGH if named * 2 >= len(chosen) else MEDIUM
            return chosen, confidence, f"titres de niveau {level} du document"
    return None


def _keywords(rows: list[Row]) -> tuple[list[int], str, str] | None:
    chosen = []
    for position, row in enumerate(rows):
        heading = chapter_heading(row.text)
        # A bare "Chapter 3" (or "第3章 …") line may be glued to its text; "Chapter 3. Then…" inside a
        # hard-wrapped paragraph is text.
        if (
            heading
            and heading.kind != "numbered"
            and (row.alone or not heading.subtitle or heading.kind == "cjk")
        ):
            chosen.append(position)
    chosen = _with_body(rows, chosen)
    if len(chosen) < 2:
        return None
    return chosen, HIGH, "lignes de titre de chapitre (« Chapitre 12 », « Prologue »…)"


def _numbered(rows: list[Row]) -> tuple[list[int], str, str] | None:
    chosen = []
    for position, row in enumerate(rows):
        heading = chapter_heading(row.text)
        if heading and heading.kind == "numbered" and row.alone and len(heading.subtitle) <= 80:
            chosen.append(position)
    chosen = _with_body(rows, chosen)
    numbers = [chapter_heading(rows[p].text).number for p in chosen]
    if len(chosen) < 3 or any(b - a != 1 for a, b in zip(numbers, numbers[1:], strict=False)):
        return None
    return chosen, MEDIUM, "lignes numérotées qui se suivent (« 1. Titre », « 2. Titre »…)"


def fill_numbers(parts: list[Part]) -> None:
    """Numbers of the parts without one, in order between their neighbours (a prologue before
    chapter 1 is 0, an epilogue after chapter 30 is 31, an interlude between 5 and 6 is 5.5)."""
    index = 0
    while index < len(parts):
        if parts[index].number is not None:
            index += 1
            continue
        end = index
        while end < len(parts) and parts[end].number is None:
            end += 1
        count = end - index
        before = parts[index - 1].number if index else None
        after = parts[end].number if end < len(parts) else None
        if after is None:
            values = [(before or 0) + step for step in range(1, count + 1)]
        elif before is None:
            values = (
                [after - count + step for step in range(count)]
                if after - count >= 0
                else [round(after * step / count, 2) for step in range(count)]
            )
        elif after - before > count and float(before).is_integer():
            values = [before + step for step in range(1, count + 1)]
        else:
            values = [
                round(before + (after - before) * step / (count + 1), 2) for step in range(1, count + 1)
            ]
        for offset, value in enumerate(values):
            parts[index + offset].number = float(value)
        index = end


def bounds(source: Source, starts: list[int]) -> list[tuple[int, int]]:
    ends = [*starts[1:], source.size]
    return list(zip(starts, ends, strict=True))


def part_text(source: Source, start: int, end: int) -> str:
    """The plain text of a part (TXT lines, Markdown lines, DOCX paragraphs one per line)."""
    if source.fmt == "docx":
        return "\n\n".join(block.text for block in source.blocks[start:end])
    return "\n".join(source.lines[start:end])


def part_file(source: Source, start: int, end: int) -> bytes:
    """The part as the file it would be if uploaded on its own."""
    if source.fmt == "docx":
        return docx_file(source.blocks[start:end])
    text = part_text(source, start, end)
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def part_name(source: Source, number: float | None, order: int) -> str:
    label = f"{number:g}" if number is not None else str(order + 1)
    return f"{stem(source.name)} ({label}).{source.fmt}"


def describe(source: Source, parts: list[Part]) -> None:
    """Size, first line, excerpt and checksum of each part, as the wizard shows them. The checksum is
    the one the chapter gets once imported (compared with the chapters already in the volume)."""
    adapter = chapter_adapter(source.fmt, 50_000_000)
    indexes = [row.index for row in source.rows]
    for order, ((start, end), part) in enumerate(
        zip(bounds(source, [p.start for p in parts]), parts, strict=True)
    ):
        text = part_text(source, start, end)
        part.characters = len(text.strip())
        inside = source.rows[bisect_left(indexes, start) : bisect_left(indexes, end)]
        rows = [row for row in inside if has_words(row.text)]
        body = rows[part.heading_rows :] if part.heading else rows
        if not part.heading:
            part.first_line = rows[0].text[:200] if rows else ""
        part.excerpt = " ".join(" ".join(row.text for row in body[:3]).split())[:EXCERPT]
        data = part_file(source, start, end)
        try:
            chapter = adapter.parse(
                part_name(source, part.number, order), data, title=part.title, resource="split"
            )
            part.checksum = chapter.checksum or ""
        except (TextRejected, ValueError):
            part.checksum = ""


def proposal(fmt: str, name: str, data: bytes) -> dict | None:
    """What the inspection of an uploaded file says about splitting it (None: nothing to split)."""
    if fmt not in SPLIT_FORMATS:
        return None
    try:
        source = read_source(fmt, name, data)
    except (TextRejected, ValueError):
        return None
    found = detect(source)
    if not found:
        return None
    return {
        "unit": "block" if fmt == "docx" else "line",
        "confidence": found.confidence,
        "reason": found.reason,
        # Applied by default only when the headings leave no doubt.
        "default": found.confidence == HIGH,
        "warnings": found.warnings,
        "parts": [
            {
                "start": part.start,
                "number": part.number,
                "number_from_heading": part.from_heading,
                "title": part.title,
                "heading": part.heading,
                "first_line": part.first_line,
                "characters": part.characters,
                "excerpt": part.excerpt,
                "checksum": part.checksum,
            }
            for part in found.parts
        ],
    }


class SplitRejected(ValueError):
    pass


def check_boundaries(source: Source, starts: list[int]) -> None:
    """Boundaries sent by a client: the start of the file first, then increasing starts of rows."""
    if not starts or starts[0] != 0:
        raise SplitRejected("Le premier chapitre découpé doit commencer au début du fichier (position 0).")
    allowed = source.starts()
    for previous, start in zip(starts, starts[1:], strict=False):
        if start <= previous:
            raise SplitRejected("Les positions des chapitres découpés doivent être croissantes.")
        if start not in allowed:
            raise SplitRejected(f"Position {start} invalide : elle ne correspond à aucune ligne du fichier.")


def api_chapters(source: Source, split: Split) -> list[dict]:
    """The parts as chapters of a JSON request: the heading is the title, the text follows it."""
    chapters = []
    for (start, end), part in zip(
        bounds(source, [part.start for part in split.parts]), split.parts, strict=True
    ):
        if source.fmt == "docx":
            blocks = source.blocks[start:end]
            if part.heading:
                skip = next(i for i, block in enumerate(blocks) if has_words(block.text))
                blocks = blocks[skip + part.heading_rows :]
            content = "\n\n".join(block.text for block in blocks)
        else:
            lines = source.lines[start:end]
            if part.heading:
                skip = next(i for i, line in enumerate(lines) if line.strip())
                taken, cut = 0, skip
                while taken < part.heading_rows and cut < len(lines):
                    if lines[cut].strip():
                        taken += 1
                    cut += 1
                lines = lines[cut:]
            content = "\n".join(lines).strip("\n") + "\n"
        chapters.append({"number": part.number, "title": part.title or FRONT_MATTER, "content": content})
    return chapters


def docx_file(blocks: list[Block]) -> bytes:
    """A minimal DOCX that `read_docx` reads back into these paragraphs, headings and list items."""
    paragraphs = []
    for block in blocks:
        properties = ""
        if block.tag in HEADINGS:
            properties = f'<w:pPr><w:outlineLvl w:val="{int(block.tag[1]) - 1}"/></w:pPr>'
        elif block.tag == "li":
            properties = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        runs = "".join(
            "<w:r><w:tab/></w:r>"
            if piece == "\t"
            else f'<w:r><w:t xml:space="preserve">{escape(piece)}</w:t></w:r>'
            for piece in re.split(r"(\t)", block.text)
            if piece
        )
        paragraphs.append(f"<w:p>{properties}{runs}</w:p>")
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>'
        + "".join(paragraphs)
        + "</w:body></w:document>"
    )
    types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in (("[Content_Types].xml", types), ("word/document.xml", document)):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return output.getvalue()


def parts_of(source: Source, chosen: list[dict]) -> list[tuple[dict, bytes, str]]:
    """The parts a person chose (`start`, `number`, `title`), each as its own file and file name."""
    starts = [item["start"] for item in chosen]
    check_boundaries(source, starts)
    result = []
    for order, ((start, end), item) in enumerate(zip(bounds(source, starts), chosen, strict=True)):
        result.append((item, part_file(source, start, end), part_name(source, item.get("number"), order)))
    return result


def is_heading_start(source: Source, start: int, end: int) -> bool:
    """Whether a part opens on a chapter heading (its first line then names the chapter)."""
    indexes = [row.index for row in source.rows]
    inside = source.rows[bisect_left(indexes, start) : bisect_left(indexes, end)]
    first = next((row for row in inside if has_words(row.text)), None)
    return bool(first and (first.level or chapter_heading(first.text)))
