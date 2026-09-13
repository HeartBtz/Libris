import io
import zipfile
from collections import defaultdict

from ebooklib import epub
from lxml import etree

from app.engines.epub.archive import inspect_archive, relative_resource, xml
from app.engines.epub.text import apply_unit, extract_units, group_units, plain

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "o": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def structure(entries: dict[str, bytes]) -> tuple[str, etree._Element, list[str]]:
    container = xml(entries["META-INF/container.xml"])
    roots = container.xpath("//c:rootfile/@full-path", namespaces=NS)
    if not roots or roots[0] not in entries:
        raise ValueError("Package OPF introuvable.")
    opf_path = roots[0]
    package = xml(entries[opf_path])
    manifest = {
        n.get("id"): relative_resource(opf_path, n.get("href", ""))
        for n in package.xpath("//o:manifest/o:item", namespaces=NS)
    }
    spine = [manifest.get(n.get("idref"), "") for n in package.xpath("//o:spine/o:itemref", namespaces=NS)]
    if not spine or any(path not in entries for path in spine):
        raise ValueError("Spine EPUB invalide ou ressource manquante.")
    return opf_path, package, list(dict.fromkeys(spine))


def parse_book(data: bytes, max_chars: int = 3500) -> dict:
    entries = inspect_archive(data)
    opf_path, package, spine = structure(entries)
    for name, value in entries.items():
        if name.endswith((".xml", ".opf", ".xhtml", ".html", ".ncx", ".svg")):
            xml(value)
    if "META-INF/encryption.xml" in entries:
        encryption = xml(entries["META-INF/encryption.xml"])
        algorithms = encryption.xpath("//*[local-name()='EncryptionMethod']/@Algorithm")
        if any(
            a not in {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}
            for a in algorithms
        ):
            raise ValueError("EPUB protégé par un chiffrement non pris en charge.")
    # Mature EPUB parser for metadata and format interpretation; never used to round-trip the archive.
    book = epub.read_epub(io.BytesIO(data), options={"ignore_ncx": False})

    def metadata(key: str, fallback: str = "") -> str:
        values = book.get_metadata("DC", key)
        return str(values[0][0]) if values else fallback

    extra = [
        relative_resource(opf_path, n.get("href", ""))
        for n in package.xpath("//o:manifest/o:item", namespaces=NS)
        if n.get("media-type") in {"application/xhtml+xml", "application/x-dtbncx+xml"}
    ]
    resources = list(dict.fromkeys([*spine, *extra]))
    chapters = []
    word_count = 0
    for path in resources:
        if path not in entries:
            raise ValueError(f"Ressource du manifest absente : {path}")
        root = xml(entries[path])
        units = extract_units(root, path)
        title_nodes = root.xpath("//*[local-name()='h1' or local-name()='h2']") or root.xpath(
            "//*[local-name()='title']"
        )
        title = "".join(title_nodes[0].itertext()).strip() if title_nodes else path
        groups = group_units(units, max_chars)
        if not groups:
            continue
        word_count += sum(len(plain(u["text"]).split()) for u in units)
        chapters.append(
            {"title": title[:500], "resource": path, "groups": groups, "narrative": path in spine}
        )
    return {
        "title": metadata("title", "Sans titre"),
        "author": metadata("creator"),
        "language": metadata("language", "en"),
        "chapters": chapters,
        "info": {
            "opf": opf_path,
            "spine": spine,
            "words": word_count,
            "images": sum(
                1
                for n in package.xpath("//o:manifest/o:item", namespaces=NS)
                if n.get("media-type", "").startswith("image/")
            ),
            "size": len(data),
            "resources": len(entries),
        },
    }


def rebuild(
    original: bytes,
    segments: list[dict],
    language: str,
    title: str | None = None,
    author: str | None = None,
    credit: str = "",
) -> bytes:
    entries = inspect_archive(original)
    opf_path, package, _ = structure(entries)
    fragments: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for segment in segments:
        translations = {u["id"]: u["text"] for u in segment["translated_units"]}
        for unit in segment["units"]:
            if unit["id"] not in translations:
                raise ValueError("Export incomplet : des passages ne sont pas traduits.")
            fragments[unit["original_id"]].append((unit, translations[unit["id"]]))
    roots: dict[str, etree._Element] = {}
    for parts in fragments.values():
        parts.sort(key=lambda p: p[0]["part"])
        unit = dict(parts[0][0])
        if [p[0]["part"] for p in parts] != list(range(unit["parts"])):
            raise ValueError("Fragments de paragraphe manquants ou dupliqués.")
        unit["text"] = "".join(p[0]["text"] for p in parts)
        resource = unit["resource"]
        if resource not in roots:
            roots[resource] = xml(entries[resource])
        apply_unit(roots[resource], unit, "".join(p[1] for p in parts))
    for path, root in roots.items():
        if etree.QName(root).localname == "html":
            root.set("lang", language)
            root.set("{http://www.w3.org/XML/1998/namespace}lang", language)
        entries[path] = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)
    for key, value in (("language", language), ("title", title), ("creator", author)):
        if value is not None:
            nodes = package.xpath(f"//dc:{key}", namespaces=NS)
            if nodes:
                nodes[0].text = value
    if credit:
        metadata = package.find("o:metadata", NS)
        etree.SubElement(metadata, f"{{{NS['dc']}}}description").text = credit
    entries[opf_path] = etree.tostring(package.getroottree(), encoding="utf-8", xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("mimetype", entries.pop("mimetype"), compress_type=zipfile.ZIP_STORED)
        for path, value in entries.items():
            archive.writestr(path, value, compress_type=zipfile.ZIP_DEFLATED)
    return output.getvalue()
