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


def export_csv(terms: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS)
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
    for mark, encoding in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if data.startswith(mark):
            return data.decode(encoding)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # Spreadsheets on Windows save "CSV" in the ANSI code page unless told otherwise.
        if fallback:
            return data.decode(fallback, errors="replace")
        raise ValueError(
            "Glossaire illisible : utilisez un fichier JSON, CSV ou TBX encodé en UTF-8."
        ) from None


def read_glossary(
    data: bytes, filename: str, source_language: str, target_language: str
) -> list[GlossaryTerm]:
    kind = glossary_format(data, filename)
    if kind == "tbx":
        items = read_tbx(data, source_language, target_language)
    elif kind == "json":
        items = json.loads(decoded(data))
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("Glossaire JSON invalide : une liste de termes [{...}, ...] est attendue.")
    else:
        items = read_csv(decoded(data, fallback="cp1252"))
    if len(items) > 10000:
        raise ValueError("Glossaire invalide ou trop volumineux.")
    terms = []
    for number, item in enumerate(items, 1):
        try:
            terms.append(GlossaryTerm.model_validate(item))
        except ValidationError as exc:
            raise ValueError(f"Terme de glossaire invalide n° {number} : {problem(exc)}") from None
    return terms


def problem(exc: ValidationError) -> str:
    return ", ".join(
        f"{'.'.join(str(part) for part in error['loc'])} ({error['type']})" for error in exc.errors()[:3]
    )


def header_key(name: str) -> str:
    name = "".join(c for c in unicodedata.normalize("NFD", name) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[_\-]+", " ", name).casefold().split())


def read_csv(text: str) -> list[dict]:
    first = next((line for line in text.splitlines() if line.strip()), "")
    delimiter = max(("\t", ";", ","), key=first.count)
    rows = [
        (reader.line_num, values)
        for reader in [csv.reader(io.StringIO(text), delimiter=delimiter)]
        for values in reader
        if any(value.strip() for value in values)
    ]
    if not rows:
        return []
    columns = {}
    for index, name in enumerate(rows[0][1]):
        key = header_key(name)
        field = next((field for field, names in HEADERS.items() if key in names), None)
        if field and field not in columns:
            columns[field] = index
    if "source" not in columns or "translation" not in columns:
        if len(rows[0][1]) < 2 or columns:
            raise ValueError("Glossaire CSV invalide : colonnes source et traduction attendues.")
        # No header at all: the first two columns are the term and its translation.
        columns = {"source": 0, "translation": 1}
    else:
        rows = rows[1:]
    items = []
    for line, values in rows:
        item = {}
        for field, index in columns.items():
            value = values[index].strip() if index < len(values) else ""
            if value.startswith("'") and value[1:].startswith(FORMULA):
                value = value[1:]
            if field in {"locked", "accepted"}:
                if value.casefold() not in TRUE | FALSE:
                    raise ValueError(f"Glossaire CSV invalide à la ligne {line} : {field} = « {value[:40]} »")
                if value:
                    item[field] = value.casefold() in TRUE
            elif value or field in {"source", "translation"}:
                item[field] = value
        items.append(item)
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
