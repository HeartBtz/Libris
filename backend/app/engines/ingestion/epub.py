"""EPUB volumes: the existing parser, its security checks, segmentation and inline codes, unchanged."""

import hashlib
import zipfile

from lxml import etree

from app.engines.epub.archive import inspect_archive
from app.engines.epub.book import NS, SEGMENTATION, parse_book, structure
from app.engines.ingestion.base import (
    FileInspection,
    ImportedAsset,
    ImportedChapter,
    ImportedVolume,
    SourceAdapter,
)
from app.engines.ingestion.naming import clean_title
from app.engines.ingestion.passages import DEFAULT_PASSAGE_CHARS


def series_metadata(package: etree._Element) -> dict:
    """Calibre `calibre:series` / `calibre:series_index` and EPUB 3 `belongs-to-collection`."""
    found: dict = {}
    for meta in package.xpath("//o:metadata/o:meta", namespaces=NS):
        name, content = meta.get("name", ""), (meta.get("content") or "").strip()
        if name == "calibre:series" and content:
            found["series"] = content[:500]
        elif name == "calibre:series_index" and content:
            try:
                found["series_index"] = float(content)
            except ValueError:
                pass
        elif meta.get("property") == "belongs-to-collection" and (meta.text or "").strip():
            found.setdefault("series", meta.text.strip()[:500])
            identifier = meta.get("id")
            if identifier:
                for refine in package.xpath(
                    "//o:metadata/o:meta[@property='group-position'][@refines=$ref]",
                    namespaces=NS,
                    ref=f"#{identifier}",
                ):
                    try:
                        found.setdefault("series_index", float((refine.text or "").strip()))
                    except ValueError:
                        pass
    return found


class EpubAdapter(SourceAdapter):
    format = "epub"
    media_type = "application/epub+zip"

    def inspect(self, name: str, data: bytes) -> FileInspection:
        found = FileInspection(name=name, format="epub", size=len(data), sha256=hashlib.sha256(data).hexdigest())
        try:
            parsed = parse_book(data)
            _, package, _ = structure(inspect_archive(data))
        except (ValueError, zipfile.BadZipFile, etree.XMLSyntaxError) as exc:
            found.errors.append(str(exc)[:500] or "EPUB illisible.")
            return found
        if not any(chapter["groups"] for chapter in parsed["chapters"]):
            found.errors.append("Aucun texte traduisible trouvé dans l’EPUB.")
        declared = series_metadata(package)
        found.title = parsed["title"][:500] or clean_title(name)
        found.author = parsed["author"][:500]
        found.language = parsed["language"][:80]
        found.series = declared.get("series", "")
        found.series_index = declared.get("series_index")
        found.meta = {
            "chapters": sum(1 for chapter in parsed["chapters"] if chapter["kind"] == "narrative"),
            "words": parsed["info"]["words"],
            "passages": sum(len(chapter["groups"]) for chapter in parsed["chapters"]),
        }
        return found

    def parse(self, name: str, data: bytes, **options) -> ImportedVolume:
        segmentation = options.get("segmentation", SEGMENTATION)
        max_chars = options.get("max_chars") or DEFAULT_PASSAGE_CHARS
        parsed = parse_book(data, max_chars=max_chars, segmentation=segmentation)
        asset = ImportedAsset(name=name, format="epub", media_type=self.media_type, data=data)
        chapters = [
            ImportedChapter(
                title=item["title"],
                resource=item["resource"],
                groups=item["groups"],
                kind=item["kind"],
                asset=asset,
            )
            for item in parsed["chapters"]
        ]
        return ImportedVolume(
            title=parsed["title"][:500] or "Sans titre",
            author=parsed["author"][:500],
            language=parsed["language"][:80] or "en",
            chapters=chapters,
            # An archive restore cuts the book again: it must use the same passage size.
            info={**parsed["info"], "passage_max_chars": max_chars},
            asset=asset,
        )
