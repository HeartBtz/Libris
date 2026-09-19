"""Glossary files: JSON, CSV (as spreadsheets write them) and TBX termbases.

TBX files follow TBX v3 (ISO 30042:2019) with TBX-Basic data categories; TBX v2 (`martif`)
termbases are read too. Libris flags travel as the standard administrative status: a locked term
is `preferredTerm`, an accepted one `admittedTerm`, a proposal not accepted `deprecatedTerm`.
"""

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field

from lxml import etree
from pydantic import ValidationError

from app.engines.epub.archive import xml
from app.schemas import GlossaryTerm

FIELDS = list(GlossaryTerm.model_fields)
FORMULA = ("=", "+", "-", "@")
TBX_NAMESPACE = "urn:iso:std:iso:30042:ed-2"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
STATUS = {"preferred": "preferredTerm-admn-sts", "admitted": "admittedTerm-admn-sts", "deprecated": "deprecatedTerm-admn-sts"}  # fmt: skip

# Column names as spreadsheets and other tools write them, in French and English.
HEADERS = {
    "source": {"source", "terme", "terme source", "source term", "term", "original", "vo", "texte source"},
    "translation": {
        "translation", "traduction", "target", "cible", "terme cible", "target term", "translated",
        "texte cible", "equivalent",
    },
    "category": {"category", "categorie", "type", "subject field", "domaine", "domain"},
    "description": {"description", "note", "notes", "commentaire", "commentaires", "comment", "definition"},
    "locked": {"locked", "verrouille", "lock", "impose", "obligatoire"},
    "accepted": {"accepted", "accepte", "valide", "approved", "approuve"},
}  # fmt: skip
TRUE = {"true", "1", "yes", "y", "oui", "o", "vrai", "x"}
FALSE = {"false", "0", "no", "n", "non", "faux", ""}


def export_json(terms: list[dict]) -> str:
    return json.dumps(terms, ensure_ascii=False, indent=2)


def export_csv(terms: list[dict], delimiter: str = ",") -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, delimiter=delimiter)
    writer.writeheader()
    for term in terms:
        # A book term must not become a spreadsheet formula; the quote is removed again on import.
        writer.writerow(
            {k: ("'" + v if isinstance(v, str) and v.startswith(FORMULA) else v) for k, v in term.items()}
        )
    return buffer.getvalue()


def export_tbx(terms: list[dict], source_language: str, target_language: str) -> bytes:
    root = etree.Element(
        f"{{{TBX_NAMESPACE}}}tbx", nsmap={None: TBX_NAMESPACE}, type="TBX-Basic", style="dca"
    )
    root.set(XML_LANG, source_language)
    header = etree.SubElement(root, "tbxHeader")
    description = etree.SubElement(etree.SubElement(header, "fileDesc"), "sourceDesc")
    etree.SubElement(description, "p").text = "Libris glossary"
    body = etree.SubElement(etree.SubElement(root, "text"), "body")
    for number, term in enumerate(terms, 1):
        entry = etree.SubElement(body, "conceptEntry", id=f"c{number}")
        if term.get("category"):
            etree.SubElement(entry, "descrip", type="subjectField").text = term["category"]
        if term.get("description"):
            etree.SubElement(entry, "descrip", type="definition").text = term["description"]
        for language, text, status in (
            (source_language, term["source"], None),
            (target_language, term["translation"], tbx_status(term)),
        ):
            section = etree.SubElement(entry, "langSec")
            section.set(XML_LANG, language)
            term_section = etree.SubElement(section, "termSec")
            etree.SubElement(term_section, "term").text = text
            if status:
                etree.SubElement(term_section, "termNote", type="administrativeStatus").text = status
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def tbx_status(term: dict) -> str:
    if not term.get("accepted", True):
        return STATUS["deprecated"]
    return STATUS["preferred"] if term.get("locked") else STATUS["admitted"]


def glossary_format(data: bytes, filename: str) -> str:
    extension = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    if extension in {"json", "csv", "tbx"}:
        return extension
    if extension in {"xml", "tbxm"}:
        return "tbx"
    start = decoded(data, fallback="cp1252").lstrip()[:1]
    return "tbx" if start == "<" else "json" if start in {"[", "{"} else "csv"


def decoded(data: bytes, fallback: str | None = None) -> str:
    return decoding(data, fallback)[0]


def decoding(data: bytes, fallback: str | None = None) -> tuple[str, str]:
    """The text and the encoding it was read with (a byte order mark wins, then UTF-8)."""
    for mark, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(mark):
            return data.decode(encoding), encoding
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        # Spreadsheets on Windows save "CSV" in the ANSI code page unless told otherwise.
        if fallback:
            return data.decode(fallback, errors="replace"), fallback
        raise ValueError(
            "Glossaire illisible : utilisez un fichier JSON, CSV ou TBX encodé en UTF-8."
        ) from None


def read_glossary(
    data: bytes, filename: str, source_language: str, target_language: str
) -> list[GlossaryTerm]:
    """Every term of the file; the first invalid one refuses the whole file."""
    parsed = parse_glossary(data, filename, source_language, target_language, strict=True)
    return [term for _, term in parsed.terms]


@dataclass
class ParsedGlossary:
    """A glossary file as read, with what was detected so a person can check it before applying."""

    format: str
    encoding: str = "utf-8"
    delimiter: str | None = None
    # CSV only: the first row's cells, whether it is a header, and the column of each field.
    columns: list[str] = field(default_factory=list)
    header: bool = False
    mapping: dict[str, int] = field(default_factory=dict)
    # (line of a CSV file, or entry number of JSON and TBX files, term)
    terms: list[tuple[int, GlossaryTerm]] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def parse_glossary(
    data: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    *,
    strict: bool = False,
    delimiter: str | None = None,
    mapping: dict[str, int] | None = None,
    header: bool | None = None,
) -> ParsedGlossary:
    """Reads a JSON, CSV or TBX glossary; with `strict`, the first invalid term raises ValueError,
    otherwise invalid rows are listed in `errors` and left out. A file that cannot be read at all
    (no source and translation columns, broken JSON or TBX) always raises."""
    kind = glossary_format(data, filename)
    parsed = ParsedGlossary(format=kind)
    if kind == "tbx":
        items = list(enumerate(read_tbx(data, source_language, target_language), 1))
    elif kind == "json":
        items = json.loads(decoded(data))
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("Glossaire JSON invalide : une liste de termes [{...}, ...] est attendue.")
        items = list(enumerate(items, 1))
    else:
        text, parsed.encoding = decoding(data, fallback="cp1252")
        items = read_csv(text, parsed, strict=strict, delimiter=delimiter, mapping=mapping, header=header)
    if len(items) > 10000:
        raise ValueError("Glossaire invalide ou trop volumineux.")
    for position, (number, item) in enumerate(items, 1):
        try:
            parsed.terms.append((number, GlossaryTerm.model_validate(item)))
        except ValidationError as exc:
            if strict:
                number = position
                raise ValueError(f"Terme de glossaire invalide n° {number} : {problem(exc)}") from None
            parsed.errors.append({"line": number, "message": f"Terme invalide : {problem(exc)}"})
    return parsed


def problem(exc: ValidationError) -> str:
    return ", ".join(
        f"{'.'.join(str(part) for part in error['loc'])} ({error['type']})" for error in exc.errors()[:3]
    )


def header_key(name: str) -> str:
    name = "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[_\-]+", " ", name).casefold().split())


DELIMITERS = ("\t", ";", ",")


def sniff_delimiter(line: str) -> str:
    """The separator of a spreadsheet export: the most frequent one outside quoted cells."""
    bare = re.sub(r'"[^"]*"', "", line)
    return max(DELIMITERS, key=bare.count)


def column_mapping(names: list[str]) -> dict[str, int]:
    """Which column holds which field, from header names in French or English."""
    columns: dict[str, int] = {}
    for index, name in enumerate(names):
        key = header_key(name)
        found = next((field for field, known in HEADERS.items() if key in known), None)
        if found and found not in columns:
            columns[found] = index
    return columns


def read_csv(
    text: str,
    parsed: ParsedGlossary,
    *,
    strict: bool = True,
    delimiter: str | None = None,
    mapping: dict[str, int] | None = None,
    header: bool | None = None,
) -> list:
    """(line, term dictionary) pairs; what was detected is written into `parsed`."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    if delimiter not in DELIMITERS:
        delimiter = sniff_delimiter(first)
    parsed.delimiter = delimiter
    rows = [
        (reader.line_num, values)
        for reader in [csv.reader(io.StringIO(text), delimiter=delimiter)]
        for values in reader
        if any(value.strip() for value in values)
    ]
    if not rows:
        return []
    parsed.columns = [value.strip() for value in rows[0][1]]
    columns = column_mapping(rows[0][1])
    detected = "source" in columns and "translation" in columns
    if mapping:
        width = max(len(values) for _, values in rows)
        columns = {
            key: index
            for key, index in mapping.items()
            if key in HEADERS and isinstance(index, int) and 0 <= index < width
        }
        if "source" not in columns or "translation" not in columns:
            raise ValueError("Glossaire CSV invalide : colonnes source et traduction attendues.")
        if columns["source"] == columns["translation"]:
            raise ValueError("Glossaire CSV invalide : colonnes source et traduction attendues.")
        has_header = detected if header is None else header
    elif not detected:
        if len(rows[0][1]) < 2 or columns:
            raise ValueError("Glossaire CSV invalide : colonnes source et traduction attendues.")
        # No header at all: the first two columns are the term and its translation.
        columns, has_header = {"source": 0, "translation": 1}, False
    else:
        has_header = True if header is None else header
    parsed.header, parsed.mapping = has_header, dict(columns)
    if has_header:
        rows = rows[1:]
    items = []
    for line, values in rows:
        item = {}
        invalid = None
        for key, index in columns.items():
            value = values[index].strip() if index < len(values) else ""
            if value.startswith("'") and value[1:].startswith(FORMULA):
                value = value[1:]
            if key in {"locked", "accepted"}:
                if value.casefold() not in TRUE | FALSE:
                    if strict:
                        raise ValueError(
                            f"Glossaire CSV invalide à la ligne {line} : {key} = « {value[:40]} »"
                        )
                    invalid = f"Valeur non reconnue pour {key} : « {value[:40]} »"
                    break
                if value:
                    item[key] = value.casefold() in TRUE
            elif value or key in {"source", "translation"}:
                item[key] = value
        if invalid:
            parsed.errors.append({"line": line, "message": invalid})
            continue
        items.append((line, item))
    return items


def local(node) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def children(node, *names: str) -> list:
    return [child for child in node if local(child) in names]


def read_tbx(data: bytes, source_language: str, target_language: str) -> list[dict]:
    root = xml(data)
    if local(root) not in {"tbx", "martif"}:
        raise ValueError("Glossaire TBX invalide : aucun terme source et cible exploitable.")
    entries = [node for node in root.iter() if local(node) in {"conceptEntry", "termEntry"}]
    items = []
    for entry in entries:
        sections = [
            (section.get(XML_LANG) or section.get("lang") or "", section)
            for section in children(entry, "langSec", "langSet")
        ]
        source = pick(sections, source_language, 0)
        target = pick(sections, target_language, 1, exclude=source)
        if source is None or target is None:
            continue
        source_term, _ = first_term(source)
        translation, status = first_term(target)
        if not source_term or not translation:
            continue
        item = {"source": source_term, "translation": translation}
        for node in [*children(entry, "descrip"), *children(source, "descrip"), *children(target, "descrip")]:
            kind, text = node.get("type"), "".join(node.itertext()).strip()
            if kind == "subjectField" and text:
                item.setdefault("category", text)
            elif kind == "definition" and text:
                item.setdefault("description", text)
        if not item.get("description"):
            notes = [" ".join("".join(n.itertext()).split()) for n in children(entry, "note")]
            if notes := [note for note in notes if note]:
                item["description"] = " ".join(notes)
        if status in {STATUS["deprecated"], "supersededTerm-admn-sts"}:
            item["accepted"] = False
        elif status == STATUS["preferred"]:
            item["locked"] = True
        items.append(item)
    if entries and not items:
        raise ValueError("Glossaire TBX invalide : aucun terme source et cible exploitable.")
    return items


def primary(language: str) -> str:
    return language.replace("_", "-").split("-")[0].casefold()


def pick(sections: list, language: str, position: int, exclude=None):
    for code, section in sections:
        if section is not exclude and primary(code) == primary(language):
            return section
    # Languages that do not match the book's: the first section is the source, the next the target.
    ordered = [section for _, section in sections]
    if position < len(ordered) and ordered[position] is not exclude:
        return ordered[position]
    return next((section for section in ordered if section is not exclude), None)


def first_term(section) -> tuple[str, str]:
    for group in [*children(section, "termSec", "tig", "ntig"), section]:
        holder = next(iter(children(group, "termGrp")), group)
        terms = children(holder, "term")
        if terms:
            status = next(
                (
                    "".join(note.itertext()).strip()
                    for note in [*children(holder, "termNote"), *children(group, "termNote")]
                    if note.get("type") == "administrativeStatus"
                ),
                "",
            )
            return " ".join("".join(terms[0].itertext()).split()), status
    return "", ""


# Import plans: what an import would add, leave alone or replace, shown before it is applied.
STRATEGIES = ("skip", "replace", "replace_all")
COMPARED = ("translation", "category", "description", "locked", "accepted")
REPORT_LIMIT = 200
EXPORT_DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t"}


def plan_import(existing: list[dict], parsed: ParsedGlossary, strategy: str = "skip") -> dict:
    """Compares the file with the terms in place (matched case-insensitively on the source).

    `skip` keeps every term in place; `replace` replaces unlocked terms that differ; `replace_all`
    replaces locked ones too. A source repeated in the file keeps its first row.
    """
    if strategy not in STRATEGIES:
        strategy = "skip"
    current = {term["source"].casefold(): term for term in existing}
    seen: dict[str, int] = {}
    new, conflicts, duplicates, actions = [], [], [], []
    unchanged = 0
    for line, term in parsed.terms:
        key = term.source.casefold()
        if key in seen:
            duplicates.append({"line": line, "source": term.source, "first_line": seen[key]})
            continue
        seen[key] = line
        incoming = term.model_dump()
        before = current.get(key)
        if before is None:
            new.append({"line": line, **incoming})
            actions.append(("add", None, incoming))
            continue
        fields = [name for name in COMPARED if before.get(name) != incoming[name]]
        if not fields:
            unchanged += 1
            continue
        locked = bool(before.get("locked"))
        replace = strategy == "replace_all" or (strategy == "replace" and not locked)
        conflicts.append(
            {
                "line": line,
                "source": term.source,
                "existing": {name: before.get(name) for name in COMPARED},
                "incoming": {name: incoming[name] for name in COMPARED},
                "fields": fields,
                "locked": locked,
                "action": "replace" if replace else "keep",
            }
        )
        if replace:
            actions.append(("replace", before.get("id"), incoming))
    replaced = sum(1 for action, _, _ in actions if action == "replace")
    return {
        "format": parsed.format,
        "encoding": parsed.encoding,
        "delimiter": parsed.delimiter,
        "columns": parsed.columns,
        "header": parsed.header,
        "mapping": parsed.mapping,
        "strategy": strategy,
        "counts": {
            "terms": len(parsed.terms),
            "new": len(new),
            "unchanged": unchanged,
            "conflicts": len(conflicts),
            "replaced": replaced,
            "kept": len(conflicts) - replaced,
            "duplicates": len(duplicates),
            "errors": len(parsed.errors),
        },
        "new": new[:REPORT_LIMIT],
        "conflicts": conflicts[:REPORT_LIMIT],
        "duplicates": duplicates[:REPORT_LIMIT],
        "errors": parsed.errors[:REPORT_LIMIT],
        "truncated": max(len(new), len(conflicts), len(duplicates), len(parsed.errors)) > REPORT_LIMIT,
        "actions": actions,
    }


def render_export(
    values: list[dict],
    format: str,
    source_language: str,
    target_language: str,
    delimiter: str = "comma",
    bom: bool = False,
) -> tuple[bytes | str, str]:
    """A glossary file and its media type; CSV can carry a byte order mark and `;` for spreadsheets."""
    if format == "json":
        return export_json(values), "application/json"
    if format == "tbx":
        return export_tbx(values, source_language, target_language), "application/x-tbx+xml"
    content = export_csv(values, EXPORT_DELIMITERS.get(delimiter, ","))
    return ("\ufeff" + content if bom else content), "text/csv"
