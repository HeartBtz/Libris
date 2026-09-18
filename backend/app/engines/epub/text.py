import hashlib
import re
from difflib import SequenceMatcher

from lxml import etree

MARKER = re.compile(r"⟦/?[tx]\d+⟧")
BLOCKS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "dt",
    "dd",
    "figcaption",
    "caption",
    "td",
    "th",
    "blockquote",
    "div",
    "section",
    "article",
}
# Ruby readings (rt) and their fallback parentheses (rp) annotate the base text: kept as they are.
EXCLUDED = {"script", "style", "svg", "math", "code", "pre", "noscript", "rt", "rp"}
ATOMIC = {"img", "br", "hr", "audio", "video", "object", "iframe"}
# Elements that break the flow of text: what sits between two of them is one unit.
STRUCTURAL = BLOCKS | {
    "body", "ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr", "figure", "aside", "header",
    "footer", "nav", "main", "details", "summary", "hgroup", "address", "fieldset", "legend", "hr",
    "pre", "center", "menu", "dialog", "form",
}  # fmt: skip
SVG = "http://www.w3.org/2000/svg"
TRANSLATED_ATTRIBUTES = ("alt", "title", "aria-label")


def tag(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
NAVIGATION = {"nav", "navMap", "navPoint", "navList", "navTarget", "docTitle", "docAuthor"}


def navigation(node: etree._Element) -> bool:
    return any(tag(n) in NAVIGATION for n in [node, *node.iterancestors()])


def page_list(node: etree._Element) -> bool:
    # Page numbers are anchors for print pagination, not text: translating "1", "2", "3" buys nothing.
    # Extraction only: books imported earlier keep rebuilding from the units they already have.
    return any(
        tag(n) == "pageList" or (tag(n) == "nav" and "page-list" in n.get(EPUB_TYPE, "").split())
        for n in [node, *node.iterancestors()]
    )


def svg_text(node: etree._Element) -> bool:
    return isinstance(node.tag, str) and node.tag == f"{{{SVG}}}text"


# Segmentation 1 (up to v0.4): books imported then keep their units, and their archives restore.
EXCLUDED_V1 = EXCLUDED - {"rt", "rp"}


def excluded(node: etree._Element, legacy: bool = False) -> bool:
    visible_svg_text = False
    for n in [node, *node.iterancestors()]:
        if n.get("translate") == "no" or "notranslate" in n.get("class", "").split():
            return True
        if legacy:
            if tag(n) in EXCLUDED_V1:
                return True
            continue
        # Text drawn by an SVG (a cover title, a map label) is read like any other: only the drawing
        # instructions around it stay untouched.
        visible_svg_text = visible_svg_text or svg_text(n)
        if tag(n) in EXCLUDED and not (tag(n) == "svg" and visible_svg_text):
            return True
    return False


def structural(node: etree._Element) -> bool:
    return isinstance(node.tag, str) and (
        tag(node) in STRUCTURAL or any(tag(d) in STRUCTURAL for d in node.iterdescendants())
    )


def leaf_block(node: etree._Element) -> bool:
    if svg_text(node):
        return True
    return tag(node) in BLOCKS and not any(tag(c) in BLOCKS for c in node.iterdescendants())


def outside_markers(value: str) -> str:
    """Text of a unit that sits outside its top-level inline elements."""
    depth, kept = 0, []
    for part in re.split(r"(⟦/?[tx]\d+⟧)", value):
        if re.fullmatch(r"⟦t\d+⟧", part):
            depth += 1
        elif re.fullmatch(r"⟦/t\d+⟧", part):
            depth -= 1
        elif depth == 0 and not re.fullmatch(r"⟦x\d+⟧", part):
            kept.append(part)
    return "".join(kept)


def validate_navigation(unit: dict, translated: str) -> None:
    # A table-of-contents entry is "<li><a>label</a></li>": text next to the link is invalid there, and
    # EPUBCheck would only say so at export time, far from the passage that caused it.
    navigation = unit.get("nav") or (
        unit.get("tag") == "li" and re.search(r"(?:nav|toc)[^/]*$", unit.get("resource", ""), re.I)
    )
    if navigation and not outside_markers(unit["text"]).strip() and outside_markers(translated).strip():
        raise ValueError(
            "Dans un sommaire, le texte doit rester à l’intérieur du lien : "
            f"« {outside_markers(translated).strip()[:80]} » est placé à côté."
        )


def linearize(root: etree._Element, legacy: bool = False) -> tuple[str, list[tuple[etree._Element, str]]]:
    """A complete linguistic unit with immutable DOM codes; slots match marker-delimited strings."""
    return _linearize(root, "text", list(root), legacy)


def run_children(parent: etree._Element, start: int) -> list:
    """Inline children after `start` (-1: the parent's own text) up to the next block-level child."""
    children = []
    for child in list(parent)[start + 1 :]:
        if structural(child):
            break
        children.append(child)
    return children


def runs(parent: etree._Element) -> list[int]:
    """Starts of the text runs of a container: its own text, then the tail of each block-level child."""
    return [-1, *(index for index, child in enumerate(parent) if structural(child))]


def linearize_run(parent: etree._Element, start: int) -> tuple[str, list[tuple[etree._Element, str]]]:
    lead = (parent, "text") if start < 0 else (parent[start], "tail")
    return _linearize(lead[0], lead[1], run_children(parent, start))


def _linearize(
    lead: etree._Element, field: str, children: list, legacy: bool = False
) -> tuple[str, list[tuple[etree._Element, str]]]:
    pieces: list[str] = []
    slots: list[tuple[etree._Element, str]] = []
    counter = 0

    def text(node: etree._Element, field: str) -> None:
        value = getattr(node, field) or ""
        if "⟦" in value or "⟧" in value:
            raise ValueError("Le texte contient les délimiteurs réservés ⟦ ⟧.")
        pieces.append(value)
        slots.append((node, field))

    def visit_children(children) -> None:
        nonlocal counter
        for child in children:
            number = counter
            counter += 1
            if not isinstance(child.tag, str) or excluded(child, legacy) or tag(child) in ATOMIC:
                pieces.append(f"⟦x{number}⟧")
            else:
                pieces.append(f"⟦t{number}⟧")
                text(child, "text")
                visit_children(list(child))
                pieces.append(f"⟦/t{number}⟧")
            text(child, "tail")

    text(lead, field)
    visit_children(children)
    return "".join(pieces), slots


def validate_codes(source: str, translated: str) -> None:
    if MARKER.findall(source) != MARKER.findall(translated):
        raise ValueError("Les marqueurs de mise en forme ont été supprimés, ajoutés ou déplacés.")
    remaining = MARKER.sub("", translated)
    if "⟦" in remaining or "⟧" in remaining:
        raise ValueError("Marqueur de mise en forme inconnu.")
    if any(ord(c) < 32 and c not in "\t\n\r" for c in translated):
        raise ValueError("Caractère XML invalide dans la traduction.")
    if any(0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in translated):
        raise ValueError("Caractère Unicode invalide dans la traduction.")


def restore_missing_codes(current: str, candidate: str) -> str | None:
    """Restore marker boundaries only when unchanged text gives an exact alignment."""
    markers = list(MARKER.finditer(current))
    if not markers:
        return None
    current_codes = [marker.group() for marker in markers]
    candidate_codes = MARKER.findall(candidate)
    if candidate_codes == current_codes:
        return candidate
    remaining_codes = iter(current_codes)
    if any(not any(code == current for current in remaining_codes) for code in candidate_codes):
        return None
    candidate = MARKER.sub("", candidate)
    if "⟦" in candidate or "⟧" in candidate:
        return None
    source = MARKER.sub("", current)
    matcher = SequenceMatcher(None, source, candidate, autojunk=False)
    opcodes = matcher.get_opcodes()
    marker_positions = []
    removed = 0
    opened: dict[str, int] = {}
    anchors: dict[int, int] = {}
    for marker in markers:
        old_position = marker.start() - removed
        removed += len(marker.group())
        marker_positions.append((old_position, marker.group()))
        name = marker.group()[1:-1]
        if name.startswith("t"):
            opened[name] = old_position
        elif name.startswith("/t"):
            start = opened.get(name[1:])
            marked_text = source[start:old_position] if start is not None else ""
            if len(marked_text) >= 3:
                candidates = [match.start() for match in re.finditer(re.escape(marked_text), candidate)]
                projected = round(start * len(candidate) / max(1, len(source)))
                ranked = sorted((abs(position - projected), position) for position in candidates)
                if ranked and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] >= len(marked_text)):
                    anchors[start] = ranked[0][1]
                    anchors[old_position] = ranked[0][1] + len(marked_text)

    def boundary(position: int) -> int | None:
        if position in anchors:
            return anchors[position]
        if position == 0:
            return 0
        if position == len(source):
            return len(candidate)
        for tag, old_start, old_end, new_start, new_end in opcodes:
            if tag == "equal" and old_start <= position <= old_end:
                return new_start + position - old_start
            if position == old_start:
                return new_start
            if position == old_end:
                return new_end
        return None

    positions = []
    for old_position, marker in marker_positions:
        new_position = boundary(old_position)
        if new_position is None:
            return None
        positions.append((new_position, marker))
    if positions != sorted(positions, key=lambda item: item[0]):
        return None
    repaired = candidate
    for position, marker in reversed(positions):
        repaired = repaired[:position] + marker + repaired[position:]
    validate_codes(current, repaired)
    return repaired


def plain(value: str) -> str:
    return MARKER.sub("", value)


def extract_units(root: etree._Element, resource: str) -> list[dict]:
    tree = root.getroottree()
    nodes = list(root.iter())
    order = {id(n): i for i, n in enumerate(nodes)}
    found: list[tuple[tuple[float, int], dict]] = []
    used: set[tuple[int, str]] = set()
    linearized: set[int] = set()

    def at(node: etree._Element, offset: float = 0) -> tuple[float, int]:
        return order[id(node)] + offset, 0

    def after(node: etree._Element) -> tuple[float, int]:
        # Text following an element (its tail) comes after everything the element contains, and the
        # tail of an inner element before the tail of the element around it.
        return order[id(list(node.iter())[-1])] + 0.5, -len(list(node.iterancestors()))

    def add(node: etree._Element, kind: str, value: str, key: tuple, attribute: str = "", **extra) -> None:
        if not plain(value).strip():
            return
        path = tree.getpath(node)
        anchor = f":{extra['run']}" if "run" in extra else ""
        identity = hashlib.sha256(f"{resource}:{path}:{kind}:{attribute}{anchor}".encode()).hexdigest()[:20]
        found.append(
            (
                key,
                {
                    "id": identity,
                    "text": value,
                    "resource": resource,
                    "path": path,
                    "kind": kind,
                    "attribute": attribute,
                    "section": "0",
                    "tag": tag(node),
                    **extra,
                    **({"nav": True} if navigation(node) else {}),
                },
            )
        )

    def claim(slots) -> None:
        used.update((id(n), field) for n, field in slots)

    html = tag(root) == "html"
    for node in nodes:
        if not isinstance(node.tag, str) or excluded(node) or page_list(node):
            continue
        inside = any(id(a) in linearized for a in node.iterancestors())
        if leaf_block(node) and not inside:
            value, slots = linearize(node)
            add(node, "block", value, at(node))
            claim(slots)
            linearized.add(id(node))
        elif html and not inside and structural(node) and tag(node) != "html":
            # Mixed content ("Dear <b>Alice</b>, …<div>sig</div>"): the text between two blocks is one
            # sentence, not one unit per text node.
            for start in runs(node):
                value, slots = linearize_run(node, start)
                key = at(node, 0.25) if start < 0 else after(node[start])
                add(node, "run", value, key, run=start)
                claim(slots)
        for attribute in TRANSLATED_ATTRIBUTES:
            if node.get(attribute):
                add(node, "attribute", node.get(attribute, ""), at(node, 0.1), attribute)
    # NCX labels and any text outside the structures above: never drop text silently.
    for node in nodes:
        for field in ("text", "tail"):
            if field == "text" and not isinstance(node.tag, str):
                continue
            parent = node if field == "text" else node.getparent()
            if parent is None or excluded(parent) or page_list(parent) or (id(node), field) in used:
                continue
            if tag(parent) in {"html", "head", "meta", "link"}:
                continue
            value = getattr(node, field) or ""
            if value.strip():
                add(node, field, value, at(node) if field == "text" else after(node))
    found.sort(key=lambda item: item[0])
    # A heading starts a section; everything after it, in document order, belongs to it.
    headings = sorted(
        (at(n), tree.getpath(n))
        for n in nodes
        if isinstance(n.tag, str) and tag(n) in {"h1", "h2", "h3", "hr"}
    )
    units, section, index = [], "0", 0
    for key, unit in found:
        while index < len(headings) and headings[index][0] <= key:
            section = headings[index][1]
            index += 1
        units.append(dict(unit, section=section))
    return units


def skipped_text(root: etree._Element) -> dict[str, int]:
    """Text kept untranslated on purpose (preformatted text, formulas, drawing labels), reported at import."""
    counts: dict[str, int] = {}
    for node in root.iter():
        name = tag(node)
        kept_kinds = {"pre", "math", "svg"}
        if name not in kept_kinds or any(tag(a) in kept_kinds for a in node.iterancestors()):
            continue
        kept = "".join(
            "".join(n.itertext()) for n in node.iter() if tag(n) in {"title", "desc"}
        ) if name == "svg" else "".join(node.itertext())
        if kept.strip():
            counts[name] = counts.get(name, 0) + 1
    return counts


SENTENCE_END = re.compile(
    # Latin scripts end a sentence before a space; CJK ones end it on the mark itself, closing quotes
    # and brackets included, since no space follows.
    r"(?<=[.!?…])(?=\s)"
    r"|(?<=[。！？｡…][」』）〕】》〉”’)\]])(?![」』）〕】》〉”’)\]])"
    r"|(?<=[。！？｡])(?![」』）〕】》〉”’)\]。！？｡…])"
)
SENTENCE_END_V1 = re.compile(r"(?<=[.!?。！？])(?=\s)")
WORD_V1 = re.compile(r"⟦/?[tx]\d+⟧|\s+|[^\s⟦]+")
CLAUSE = re.compile(r"⟦/?[tx]\d+⟧|\s+|[^\s⟦、，,；;]+[、，,；;]?|[、，,；;]")


def extract_units_v1(root: etree._Element, resource: str) -> list[dict]:
    """Segmentation 1, kept verbatim: restoring an archive re-cuts its EPUB exactly as it was cut."""
    tree = root.getroottree()
    units: list[dict] = []
    used: set[tuple[str, str]] = set()
    section = "0"

    def add(node: etree._Element, kind: str, value: str, attribute: str = "") -> None:
        if not plain(value).strip():
            return
        path = tree.getpath(node)
        identity = hashlib.sha256(f"{resource}:{path}:{kind}:{attribute}".encode()).hexdigest()[:20]
        units.append(
            {
                "id": identity,
                "text": value,
                "resource": resource,
                "path": path,
                "kind": kind,
                "attribute": attribute,
                "section": section,
                "tag": tag(node),
                **({"nav": True} if navigation(node) else {}),
            }
        )

    for node in root.iter():
        if not isinstance(node.tag, str) or excluded(node, True) or page_list(node):
            continue
        name = tag(node)
        if name in {"h1", "h2", "h3", "hr"}:
            section = tree.getpath(node)
        if name in BLOCKS and not any(tag(c) in BLOCKS for c in node.iterdescendants()):
            value, slots = linearize(node, legacy=True)
            add(node, "block", value)
            used.update((tree.getpath(n), field) for n, field in slots)
        for attribute in ("alt", "title"):
            if node.get(attribute):
                add(node, "attribute", node.get(attribute, ""), attribute)
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        for field in ("text", "tail"):
            parent = node if field == "text" else node.getparent()
            if parent is None or excluded(parent, True) or page_list(parent):
                continue
            if (tree.getpath(node), field) in used:
                continue
            if tag(parent) in {"html", "head", "meta", "link"}:
                continue
            value = getattr(node, field) or ""
            if value.strip():
                add(node, field, value)
    order = {tree.getpath(n): i for i, n in enumerate(root.iter())}
    units.sort(key=lambda u: (order[u["path"]], u["kind"] == "attribute"))
    return units


def split_unit(unit: dict, max_chars: int, legacy: bool = False) -> list[dict]:
    value = unit["text"]
    if len(value) <= max_chars:
        return [dict(unit, part=0, parts=1, original_id=unit["id"])]
    # Prefer sentence boundaries, then clauses and words; a run without any (CJK prose) is cut at the
    # limit. Codes are never split.
    sentences = (SENTENCE_END_V1 if legacy else SENTENCE_END).split(value)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        atoms = (
            [sentence]
            if len(sentence) <= max_chars
            else (WORD_V1 if legacy else CLAUSE).findall(sentence)
        )
        for atom in atoms:
            chunks = (
                [atom]
                if legacy or len(atom) <= max_chars or MARKER.fullmatch(atom)
                else [atom[i : i + max_chars] for i in range(0, len(atom), max_chars)]
            )
            for chunk in chunks:
                if current and len(current) + len(chunk) > max_chars:
                    pieces.append(current)
                    current = ""
                current += chunk
    if current:
        pieces.append(current)
    if "".join(pieces) != value:
        raise ValueError("Découpage non réversible.")
    return [
        dict(unit, id=f"{unit['id']}~{i}", original_id=unit["id"], text=text, part=i, parts=len(pieces))
        for i, text in enumerate(pieces)
    ]


def group_units(units: list[dict], max_chars: int = 3500, legacy: bool = False) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for unit in units:
        for part in split_unit(unit, max_chars, legacy):
            if current and (
                size + len(part["text"]) > max_chars
                or current[-1]["section"] != part["section"]
                or part["tag"] in {"h1", "h2", "h3"}
            ):
                groups.append(current)
                current, size = [], 0
            current.append(part)
            size += len(part["text"])
    if current:
        groups.append(current)
    return groups


def apply_unit(root: etree._Element, unit: dict, translation: str, namespaces: dict | None = None) -> None:
    prefixes = namespaces if namespaces is not None else {k: v for k, v in root.nsmap.items() if k}
    matches = root.getroottree().xpath(unit["path"], namespaces=prefixes)
    if len(matches) != 1:
        raise ValueError("Ancre DOM source introuvable ou ambiguë.")
    node = matches[0]
    validate_codes(unit["text"], translation)
    if unit["kind"] in {"block", "run"}:
        value, slots = linearize(node) if unit["kind"] == "block" else linearize_run(node, unit["run"])
        if MARKER.findall(value) != MARKER.findall(unit["text"]) and unit["kind"] == "block":
            # A unit cut before ruby readings were kept out of the text (segmentation 1).
            value, slots = linearize(node, legacy=True)
        strings = MARKER.split(translation)
        if len(strings) != len(slots):
            raise ValueError("Nombre de fragments inline incohérent.")
        for (target, field), value in zip(slots, strings, strict=True):
            setattr(target, field, value)
    elif unit["kind"] == "attribute":
        node.set(unit["attribute"], translation)
    else:
        setattr(node, unit["kind"], translation)
