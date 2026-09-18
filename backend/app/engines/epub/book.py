import hashlib
import io
import re
import zipfile
from collections import defaultdict
from urllib.parse import unquote, urlsplit, urlunsplit

from lxml import etree

from app.engines.epub.archive import inspect_archive, relative_resource, xml
from app.engines.epub.text import apply_unit, extract_units, group_units, plain, skipped_text
from app.languages import primary, right_to_left

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "o": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
EPUB_NS = "http://www.idpf.org/2007/ops"
HTML5_SECTIONING = {
    "article",
    "aside",
    "figcaption",
    "figure",
    "footer",
    "header",
    "main",
    "nav",
    "section",
}


def _safe_xml_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]", "-", value)
    return value if value and re.match(r"[A-Za-z_]", value[0]) else f"id-{value}"


def _rewrite_fragments(
    root: etree._Element, resource: str, identifiers: dict[tuple[str, str], str]
) -> None:
    for node in root.iter():
        for attribute in ("href", "src"):
            value = node.get(attribute)
            if not value:
                continue
            parsed = urlsplit(value)
            if not parsed.fragment or parsed.scheme or parsed.netloc:
                continue
            try:
                target = resource if not parsed.path else relative_resource(resource, parsed.path)
            except ValueError:
                continue
            replacement = identifiers.get((target, unquote(parsed.fragment)))
            if replacement:
                node.set(attribute, urlunsplit(parsed._replace(fragment=replacement)))


def _normalize_epub2(entries: dict[str, bytes], opf_path: str, package: etree._Element) -> None:
    if not package.get("version", "").startswith("2."):
        return

    for spine in package.xpath("//o:spine", namespaces=NS):
        spine.attrib.pop("page-progression-direction", None)

    content_paths = list(
        dict.fromkeys(
            relative_resource(opf_path, item.get("href", ""))
            for item in package.xpath("//o:manifest/o:item", namespaces=NS)
            if item.get("media-type") == "application/xhtml+xml"
        )
    )
    roots: dict[str, etree._Element] = {}
    identifiers: dict[tuple[str, str], str] = {}
    for path in content_paths:
        if path not in entries:
            continue
        root = xml(entries[path])
        used: set[str] = set()
        for node in root.xpath("//*[@id]"):
            original = node.get("id", "")
            base = _safe_xml_id(original)
            candidate = base
            suffix = 2
            while candidate in used:
                candidate = f"{base}-{suffix}"
                suffix += 1
            used.add(candidate)
            node.set("id", candidate)
            identifiers.setdefault((path, original), candidate)
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            name = etree.QName(node).localname
            if name in HTML5_SECTIONING:
                namespace = etree.QName(node).namespace
                node.tag = f"{{{namespace}}}div" if namespace else "div"
            node.attrib.pop(f"{{{EPUB_NS}}}type", None)
            node.attrib.pop("hidden", None)
            for attribute in list(node.attrib):
                if attribute.lower().startswith("data-"):
                    del node.attrib[attribute]
            if name == "li":
                node.attrib.pop("value", None)
        etree.cleanup_namespaces(root)
        roots[path] = root

    for path, root in roots.items():
        _rewrite_fragments(root, path, identifiers)
        entries[path] = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)
    for path, value in list(entries.items()):
        if path in roots or not path.endswith(".ncx"):
            continue
        root = xml(value)
        _rewrite_fragments(root, path, identifiers)
        entries[path] = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)
    _rewrite_fragments(package, opf_path, identifiers)


def _remove_preserving_tail(node: etree._Element) -> None:
    parent = node.getparent()
    previous = node.getprevious()
    if node.tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def _is_scripted(root: etree._Element) -> bool:
    """EPUB 3 `scripted` property: executable scripts, event handlers or HTML forms."""
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        name = etree.QName(node).localname
        if name == "form":
            return True
        if name == "script":
            kind = node.get("type", "").split(";")[0].strip().lower()
            if not kind or kind == "module" or kind.endswith(("javascript", "ecmascript")):
                return True
        if any(attribute.lower().startswith("on") for attribute in node.attrib if "}" not in attribute):
            return True
    return False


def _normalize_inherited_defects(
    entries: dict[str, bytes], opf_path: str, package: etree._Element, title: str
) -> None:
    """Repair unambiguous source defects that EPUBCheck rejects, whatever the translation."""
    epub3 = package.get("version", "").startswith("3.")
    spine_ids = set(package.xpath("//o:spine/o:itemref/@idref", namespaces=NS))
    for item in package.xpath("//o:manifest/o:item", namespaces=NS):
        if item.get("id") not in spine_ids:
            # A manifest entry whose file was deleted (fonts, images) is declared but can never load.
            try:
                dangling = relative_resource(opf_path, item.get("href", "")) not in entries
            except ValueError:
                dangling = False
            if dangling:
                _remove_preserving_tail(item)
                continue
        if item.get("media-type") != "application/xhtml+xml":
            continue
        path = relative_resource(opf_path, item.get("href", ""))
        if path not in entries:
            continue
        root = xml(entries[path])
        changed = False
        # A script whose local target is absent can never run; conversion tools leave such stubs.
        for script in root.xpath("//*[local-name()='script'][@src]"):
            parsed = urlsplit(script.get("src", ""))
            if parsed.scheme or parsed.netloc:
                continue
            try:
                missing = relative_resource(path, parsed.path) not in entries
            except ValueError:
                missing = True
            if missing:
                _remove_preserving_tail(script)
                changed = True
        if epub3:
            for node in root.xpath("//*[local-name()='head']/*[local-name()='title']"):
                if not "".join(node.itertext()).strip() and title:
                    node.text = title
                    changed = True
            properties = item.get("properties", "").split()
            scripted = _is_scripted(root)
            if scripted != ("scripted" in properties):
                properties = [*properties, "scripted"] if scripted else [p for p in properties if p != "scripted"]
                if properties:
                    item.set("properties", " ".join(properties))
                else:
                    item.attrib.pop("properties", None)
        if changed:
            entries[path] = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)

    name = package.get("unique-identifier")
    identifiers = package.xpath("//dc:identifier[@id=$name]", namespaces=NS, name=name) if name else []
    identifier = (identifiers[0].text or "").strip() if identifiers else ""
    if not identifier:
        return
    for item in package.xpath("//o:manifest/o:item[@media-type='application/x-dtbncx+xml']", namespaces=NS):
        path = relative_resource(opf_path, item.get("href", ""))
        if path not in entries:
            continue
        root = xml(entries[path])
        stale = [
            meta
            for meta in root.xpath("//*[local-name()='head']/*[local-name()='meta'][@name='dtb:uid']")
            if meta.get("content", "").strip() != identifier
        ]
        for meta in stale:
            meta.set("content", identifier)
        if stale:
            entries[path] = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)


def metadata_units(package: etree._Element, opf_path: str) -> list[dict]:
    """The blurb (and short subjects) readers display: translated like any passage, never lost."""
    tree = package.getroottree()
    units = []
    for key, limit in (("description", 20000), ("subject", 200)):
        for node in package.xpath(f"//o:metadata/dc:{key}", namespaces=NS):
            value = node.text or ""
            if not value.strip() or len(node) or len(value) > limit or "⟦" in value or "⟧" in value:
                continue
            path = tree.getpath(node)
            units.append(
                {
                    "id": hashlib.sha256(f"{opf_path}:{path}:text:".encode()).hexdigest()[:20],
                    "text": value,
                    "resource": opf_path,
                    "path": path,
                    "kind": "text",
                    "attribute": "",
                    "section": "metadata",
                    "tag": key,
                }
            )
    return units


def namespaces_of(root: etree._Element) -> dict[str, str]:
    return {
        prefix: uri
        for node in root.iter()
        if isinstance(node.tag, str)
        for prefix, uri in node.nsmap.items()
        if prefix
    }


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def declared_language(node: etree._Element) -> str | None:
    return node.get(XML_LANG) or node.get("lang")


def orient(root: etree._Element, source: str, target: str) -> None:
    """Language and direction of a translated document: the source's must not survive on translated text.

    An element declaring the source language now holds target text; one declaring another language
    (a Latin motto, a German greeting) was left as it is and keeps its language and direction.
    """
    source = declared_language(root) or source
    direction = "rtl" if right_to_left(target) else "ltr"
    root.set("lang", target)
    root.set(XML_LANG, target)
    if direction == "rtl" or root.get("dir"):
        root.set("dir", direction)
    own = {primary(source), primary(target)}
    for node in root.iter():
        if node is root or not isinstance(node.tag, str):
            continue
        language = declared_language(node)
        if language is not None and primary(language) == primary(source):
            for attribute in ("lang", XML_LANG):
                if node.get(attribute) is not None:
                    node.set(attribute, target)
        foreign = any(
            declared_language(n) and primary(declared_language(n)) not in own
            for n in [node, *node.iterancestors()]
        )
        if node.get("dir") in {"ltr", "rtl"} and node.get("dir") != direction and not foreign:
            node.set("dir", direction)


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
    missing = [path or "(référence inconnue)" for path in spine if path not in entries]
    if not spine or missing:
        detail = f" : {', '.join(missing[:5])}" if missing else ""
        raise ValueError(f"Spine EPUB invalide ou document absent de l’archive{detail}.")
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
    def metadata(key: str, fallback: str = "") -> str:
        # Read from the package already parsed: a second parser used to fail (HTTP 500) on a manifest
        # entry missing from the archive, for three strings.
        return package.xpath(f"string(//dc:{key}[1])", namespaces=NS).strip() or fallback

    items = package.xpath("//o:manifest/o:item", namespaces=NS)
    extra = [
        relative_resource(opf_path, n.get("href", ""))
        for n in items
        if n.get("media-type") in {"application/xhtml+xml", "application/x-dtbncx+xml"}
    ]
    navigation = {
        relative_resource(opf_path, n.get("href", ""))
        for n in items
        if "nav" in n.get("properties", "").split() or n.get("media-type") == "application/x-dtbncx+xml"
    }
    manifest = {n.get("id"): relative_resource(opf_path, n.get("href", "")) for n in items}
    # linear="no" documents (notes, cover pages) sit outside the reading order: after the story, so
    # that they neither interrupt nor seed its narrative context.
    auxiliary = {
        manifest.get(ref.get("idref"), "")
        for ref in package.xpath("//o:spine/o:itemref[@linear='no']", namespaces=NS)
    }
    story = [path for path in spine if path not in auxiliary and path not in navigation]
    resources = list(dict.fromkeys([*story, *spine, *extra]))
    chapters = []
    word_count = 0
    untranslated: dict[str, dict] = {}
    for path in resources:
        if path not in entries:
            continue  # Spine documents were checked; a dangling entry elsewhere does not prevent translation.
        root = xml(entries[path])
        units = extract_units(root, path)
        for kind, count in skipped_text(root).items():
            entry = untranslated.setdefault(kind, {"count": 0, "resources": []})
            entry["count"] += count
            entry["resources"] = [*entry["resources"], path][:20]
        title_nodes = root.xpath("//*[local-name()='h1' or local-name()='h2']") or root.xpath(
            "//*[local-name()='title']"
        )
        title = "".join(title_nodes[0].itertext()).strip() if title_nodes else path
        groups = group_units(units, max_chars)
        if not groups:
            continue
        word_count += sum(len(plain(u["text"]).split()) for u in units)
        kind = "navigation" if path in navigation else "auxiliary" if path not in story else "narrative"
        chapters.append(
            {
                "title": title[:500],
                "resource": path,
                "groups": groups,
                "narrative": kind == "narrative",
                "kind": kind,
            }
        )
    described = metadata_units(package, opf_path)
    if described:
        chapters.append(
            {
                "title": "Métadonnées du livre",
                "resource": opf_path,
                "groups": group_units(described, max_chars),
                "narrative": False,
                "kind": "metadata",
            }
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
            # Kept as in the original on purpose; listed so that nothing disappears without a word.
            "untranslated": untranslated,
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
    roots: dict[str, etree._Element] = {opf_path: package}
    prefixes = {opf_path: namespaces_of(package)}
    for parts in fragments.values():
        parts.sort(key=lambda p: p[0]["part"])
        unit = dict(parts[0][0])
        if [p[0]["part"] for p in parts] != list(range(unit["parts"])):
            raise ValueError("Fragments de paragraphe manquants ou dupliqués.")
        unit["text"] = "".join(p[0]["text"] for p in parts)
        resource = unit["resource"]
        if resource not in roots:
            roots[resource] = xml(entries[resource])
        apply_unit(roots[resource], unit, "".join(p[1] for p in parts), prefixes.get(resource))
    source_language = package.xpath("string(//dc:language[1])", namespaces=NS).strip()
    for path, root in roots.items():
        if path == opf_path:
            continue  # the package is written once, after the metadata below
        if etree.QName(root).localname == "html":
            orient(root, source_language, language)
        entries[path] = etree.tostring(root.getroottree(), encoding="utf-8", xml_declaration=True)
    direction = "rtl" if right_to_left(language) else "ltr"
    for spine in package.xpath("//o:spine", namespaces=NS):
        # Reading systems turn pages the way the script runs; EPUB 2 has no such attribute (removed below).
        if direction == "rtl" or spine.get("page-progression-direction") == "rtl":
            spine.set("page-progression-direction", direction)
    _normalize_epub2(entries, opf_path, package)
    declared_title = package.xpath("string(//dc:title[1])", namespaces=NS).strip()
    _normalize_inherited_defects(entries, opf_path, package, title or declared_title)
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
