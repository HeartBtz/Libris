import hashlib
import re

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
EXCLUDED = {"script", "style", "svg", "math", "code", "pre", "noscript"}
ATOMIC = {"img", "br", "hr", "audio", "video", "object", "iframe"}


def tag(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def excluded(node: etree._Element) -> bool:
    return any(
        tag(n) in EXCLUDED or n.get("translate") == "no" or "notranslate" in n.get("class", "").split()
        for n in [node, *node.iterancestors()]
    )


def linearize(root: etree._Element) -> tuple[str, list[tuple[etree._Element, str]]]:
    """A complete linguistic unit with immutable DOM codes; slots match marker-delimited strings."""
    pieces: list[str] = []
    slots: list[tuple[etree._Element, str]] = []
    counter = 0

    def text(node: etree._Element, field: str) -> None:
        value = getattr(node, field) or ""
        if "⟦" in value or "⟧" in value:
            raise ValueError("Le texte contient les délimiteurs réservés ⟦ ⟧.")
        pieces.append(value)
        slots.append((node, field))

    def visit(node: etree._Element) -> None:
        nonlocal counter
        text(node, "text")
        for child in node:
            number = counter
            counter += 1
            if not isinstance(child.tag, str) or excluded(child) or tag(child) in ATOMIC:
                pieces.append(f"⟦x{number}⟧")
            else:
                pieces.append(f"⟦t{number}⟧")
                visit(child)
                pieces.append(f"⟦/t{number}⟧")
            text(child, "tail")

    visit(root)
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


def plain(value: str) -> str:
    return MARKER.sub("", value)


def extract_units(root: etree._Element, resource: str) -> list[dict]:
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
            }
        )

    for node in root.iter():
        if not isinstance(node.tag, str) or excluded(node):
            continue
        name = tag(node)
        if name in {"h1", "h2", "h3", "hr"}:
            section = tree.getpath(node)
        if name in BLOCKS and not any(tag(c) in BLOCKS for c in node.iterdescendants()):
            value, slots = linearize(node)
            add(node, "block", value)
            used.update((tree.getpath(n), field) for n, field in slots)
        for attribute in ("alt", "title"):
            if node.get(attribute):
                add(node, "attribute", node.get(attribute, ""), attribute)
    # Orphan text and NCX labels: never drop mixed-content text outside usual paragraph tags.
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        for field in ("text", "tail"):
            parent = node if field == "text" else node.getparent()
            if parent is None or excluded(parent) or (tree.getpath(node), field) in used:
                continue
            if tag(parent) in {"html", "head", "meta", "link"}:
                continue
            value = getattr(node, field) or ""
            if value.strip():
                add(node, field, value)
    # DOM order, with attributes after the element's visible text.
    order = {tree.getpath(n): i for i, n in enumerate(root.iter())}
    units.sort(key=lambda u: (order[u["path"]], u["kind"] == "attribute"))
    return units


def split_unit(unit: dict, max_chars: int) -> list[dict]:
    value = unit["text"]
    if len(value) <= max_chars:
        return [dict(unit, part=0, parts=1, original_id=unit["id"])]
    # Prefer sentence boundaries. Long sentences fall back to word boundaries, never split codes.
    sentences = re.split(r"(?<=[.!?。！？])(?=\s)", value)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        atoms = [sentence] if len(sentence) <= max_chars else re.findall(r"⟦/?[tx]\d+⟧|\s+|[^\s⟦]+", sentence)
        for atom in atoms:
            if current and len(current) + len(atom) > max_chars:
                pieces.append(current)
                current = ""
            current += atom
    if current:
        pieces.append(current)
    if "".join(pieces) != value:
        raise ValueError("Découpage non réversible.")
    return [
        dict(unit, id=f"{unit['id']}~{i}", original_id=unit["id"], text=text, part=i, parts=len(pieces))
        for i, text in enumerate(pieces)
    ]


def group_units(units: list[dict], max_chars: int = 3500) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for unit in units:
        for part in split_unit(unit, max_chars):
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


def apply_unit(root: etree._Element, unit: dict, translation: str) -> None:
    matches = root.getroottree().xpath(unit["path"], namespaces={k: v for k, v in root.nsmap.items() if k})
    if len(matches) != 1:
        raise ValueError("Ancre DOM source introuvable ou ambiguë.")
    node = matches[0]
    validate_codes(unit["text"], translation)
    if unit["kind"] == "block":
        _, slots = linearize(node)
        strings = MARKER.split(translation)
        if len(strings) != len(slots):
            raise ValueError("Nombre de fragments inline incohérent.")
        for (target, field), value in zip(slots, strings, strict=True):
            setattr(target, field, value)
    elif unit["kind"] == "attribute":
        node.set(unit["attribute"], translation)
    else:
        setattr(node, unit["kind"], translation)
