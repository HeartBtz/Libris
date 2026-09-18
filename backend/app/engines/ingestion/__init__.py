from app.engines.ingestion.base import (
    FileInspection,
    ImportedAsset,
    ImportedChapter,
    ImportedUnit,
    ImportedVolume,
    ImportedWork,
    SourceAdapter,
)
from app.engines.ingestion.epub import EpubAdapter
from app.engines.ingestion.text import TextRejected, TxtAdapter

ADAPTERS = {"epub": EpubAdapter, "txt": TxtAdapter}

__all__ = [
    "ADAPTERS",
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
