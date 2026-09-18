"""The shape every source format is turned into before it reaches the pipeline.

Analysis, context, translation, quality and review only see chapters made of passages (groups of
units `{"id", "text", ...}`); the adapters are the only place that knows what an EPUB, a TXT file or a
JSON payload looks like. Exports read `Chapter.import_meta["layout"]` for non-EPUB sources.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypedDict

ADAPTER_VERSION = 1


class ImportedUnit(TypedDict, total=False):
    id: str
    text: str
    resource: str
    path: str
    kind: str
    attribute: str
    section: str
    tag: str
    part: int
    parts: int
    original_id: str


@dataclass
class ImportedAsset:
    name: str
    format: str
    media_type: str
    data: bytes
    meta: dict = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass
class ImportedChapter:
    title: str
    resource: str
    groups: list[list[ImportedUnit]]
    kind: str = "narrative"
    number: float | None = None
    external_id: str | None = None
    checksum: str | None = None
    meta: dict = field(default_factory=dict)
    asset: ImportedAsset | None = None


@dataclass
class ImportedVolume:
    title: str
    author: str
    language: str
    chapters: list[ImportedChapter]
    info: dict = field(default_factory=dict)
    number: int | None = None
    external_id: str | None = None
    asset: ImportedAsset | None = None


@dataclass
class ImportedWork:
    series_name: str
    volumes: list[ImportedVolume]


@dataclass
class FileInspection:
    """What an adapter reads from one file without creating anything."""

    name: str
    format: str
    size: int
    sha256: str
    title: str = ""
    author: str = ""
    language: str = ""
    series: str = ""
    series_index: float | None = None
    chapter_number: float | None = None
    number_confidence: str = "low"
    number_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class SourceAdapter(ABC):
    format: str
    media_type: str

    @abstractmethod
    def inspect(self, name: str, data: bytes) -> FileInspection:
        """Metadata and problems of one file; never creates a project."""

    @abstractmethod
    def parse(self, name: str, data: bytes, **options):
        """The normalized content of one file."""
