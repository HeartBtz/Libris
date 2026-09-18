import io
import posixpath
import re
import stat
import zipfile
from html.entities import name2codepoint
from urllib.parse import unquote, urlsplit

from lxml import etree

from app.config import settings


def safe_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise ValueError("Chemin ZIP non sûr.")
    if any(part == ".." for part in name.split("/")) or re.match(r"^[A-Za-z]:", name):
        raise ValueError("Traversée de répertoire détectée dans l’archive.")
    return posixpath.normpath(name)


def relative_resource(base: str, href: str) -> str:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise ValueError("Une ressource structurelle EPUB doit être locale.")
    path = unquote(parsed.path)
    if path.startswith("/") or "\\" in path:
        raise ValueError("Référence EPUB non sûre.")
    return safe_name(posixpath.normpath(posixpath.join(posixpath.dirname(base), path)))


XML_ENTITIES = {b"amp", b"lt", b"gt", b"quot", b"apos"}


def numeric_entities(data: bytes) -> bytes:
    # EPUB 2 relies on the XHTML DTD for &nbsp; and friends. The DTD stays unread: the named HTML
    # entities are a fixed table, turned into numeric references that any XML parser accepts.
    def replace(match: re.Match) -> bytes:
        name = match.group(1)
        code = None if name in XML_ENTITIES else name2codepoint.get(name.decode("ascii"))
        return match.group(0) if code is None else b"&#%d;" % code

    return re.sub(rb"&([A-Za-z][A-Za-z0-9]{1,31});", replace, data) if b"&" in data else data


def xml(data: bytes) -> etree._Element:
    if b"<!ENTITY" in data.upper():
        raise ValueError("Les déclarations d’entités XML ne sont pas autorisées.")
    data = numeric_entities(data)
    root = etree.fromstring(
        data,
        etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=False, huge_tree=False),
    )
    if any(isinstance(node, etree._Entity) for node in root.iter()):
        raise ValueError("Entité XML non résolue.")
    return root


def inspect_archive(data: bytes) -> dict[str, bytes]:
    limits = settings()
    if len(data) > limits.max_upload_mb * 1024**2:
        raise ValueError("Fichier importé trop volumineux.")
    total = 0
    entries: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if len(archive.infolist()) > limits.max_entries:
            raise ValueError("Trop de fichiers dans l’archive.")
        for info in archive.infolist():
            name = safe_name(info.filename)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or info.flag_bits & 1:
                raise ValueError("Liens symboliques et archives ZIP chiffrées non pris en charge.")
            if info.is_dir():
                continue
            if name in entries:
                raise ValueError("Noms de fichiers dupliqués dans l’archive.")
            total += info.file_size
            if total > limits.max_unpacked_mb * 1024**2 or info.file_size > 50 * 1024**2:
                raise ValueError("Limite de décompression dépassée (archive bomb possible).")
            if info.file_size > 1024**2 and info.file_size / max(info.compress_size, 1) > 1000:
                raise ValueError("Ratio de compression excessif.")
            with archive.open(info) as stream:
                value = stream.read(info.file_size + 1)
            if len(value) != info.file_size:
                raise ValueError("Taille ZIP incohérente.")
            entries[name] = value
    if entries.get("mimetype") != b"application/epub+zip":
        raise ValueError("Ce fichier n’est pas un EPUB : mimetype absent ou incorrect.")
    if "META-INF/container.xml" not in entries:
        raise ValueError("EPUB sans META-INF/container.xml.")
    return entries
