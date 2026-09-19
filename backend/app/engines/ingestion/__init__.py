from app.engines.ingestion.base import (
    FileInspection,
    ImportedAsset,
    ImportedChapter,
    ImportedUnit,
    ImportedVolume,
    ImportedWork,
    SourceAdapter,
)
from app.engines.ingestion.docx import DocxAdapter
from app.engines.ingestion.epub import EpubAdapter
from app.engines.ingestion.html import HtmlAdapter
from app.engines.ingestion.markdown import MarkdownAdapter
from app.engines.ingestion.text import TextRejected, TxtAdapter

ADAPTERS = {"epub": EpubAdapter, "txt": TxtAdapter, "md": MarkdownAdapter, "html": HtmlAdapter, "docx": DocxAdapter}
# Chapter formats: one file is one chapter of a series (TXT and the structured documents).
CHAPTER_FORMATS = ("txt", "md", "html", "docx")
# File extensions accepted for each import format.
UPLOAD_EXTENSIONS = {
    "epub": ("epub",),
    "txt": ("txt",),
    "md": ("md", "markdown"),
    "html": ("html", "htm", "xhtml"),
    "docx": ("docx",),
}


def chapter_adapter(fmt: str, max_length: int):
    """The adapter reading one chapter file of this import format."""
    return ADAPTERS[fmt](max_length)

__all__ = [
    "ADAPTERS",
    "CHAPTER_FORMATS",
    "UPLOAD_EXTENSIONS",
    "DocxAdapter",
    "HtmlAdapter",
    "MarkdownAdapter",
    "chapter_adapter",
    "EpubAdapter",
    "FileInspection",
    "ImportedAsset",
    "ImportedChapter",
    "ImportedUnit",
    "ImportedVolume",
    "ImportedWork",
    "SourceAdapter",
    "TextRejected",
    "TxtAdapter",
]
