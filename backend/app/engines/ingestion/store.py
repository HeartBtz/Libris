"""Writing normalized sources into SQL: series, volumes, chapters, passages and their source files.

SQL stays the source of truth; files are written under DATA_DIR at paths Libris chooses (never a name
taken from the upload) and removed again when the transaction does not commit.
"""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.engines.epub.text import plain
from app.engines.ingestion.base import ADAPTER_VERSION, ImportedAsset, ImportedChapter, ImportedVolume
from app.engines.ingestion.naming import display_series, normalize_series
from app.engines.ingestion.text import chapter_key
from app.engines.translation.memory import memory_key
from app.jobs.queue import ACTIVE, emit
from app.models import (
    Chapter,
    CharacterRelation,
    Entity,
    Job,
    Memory,
    Outbox,
    Project,
    RequestLog,
    Segment,
    Series,
    SeriesEntity,
    SeriesRelation,
    SourceAsset,
    TranslationVersion,
)
from app.models.common import uid

EXTENSIONS = {"epub": "epub", "txt": "txt", "json": "json"}


@dataclass
class Files:
    """Files written by an import; removed if the database transaction is rolled back."""

    written: list[Path] = field(default_factory=list)
    obsolete: list[Path] = field(default_factory=list)

    def write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.written.append(path)

    def discard(self) -> None:
        for path in self.written:
            path.unlink(missing_ok=True)

    def committed(self) -> None:
        for path in self.obsolete:
            path.unlink(missing_ok=True)


def data_path(relative: str) -> Path:
    root = settings().data_dir.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError("Chemin de stockage hors du dossier de données.")
    return path


def asset_file(asset: SourceAsset) -> Path | None:
    """The stored file: relative paths live under DATA_DIR; books imported before 0.6 keep their
    absolute path, with their deterministic place under DATA_DIR/books as a fallback."""
    candidates = []
    stored = Path(asset.storage_path)
    if stored.is_absolute():
        candidates.append(stored)
    else:
        candidates.append(data_path(asset.storage_path))
    fallback = (asset.meta or {}).get("fallback_path")
    if fallback:
        candidates.append(data_path(fallback))
    return next((path for path in candidates if path.is_file()), None)


def read_asset(asset: SourceAsset) -> bytes | None:
    path = asset_file(asset)
    return path.read_bytes() if path else None


def store_asset(db: Session, project: Project, imported: ImportedAsset, files: Files) -> SourceAsset:
    asset_id = uid()
    # EPUB volumes keep their 0.5 place, which older code paths and backups already know.
    relative = (
        f"books/{project.id}.epub"
        if imported.format == "epub"
        else f"sources/{project.id}/{asset_id}.{EXTENSIONS[imported.format]}"
    )
    asset = SourceAsset(
        id=asset_id,
        project_id=project.id,
        format=imported.format,
        original_name=safe_display_name(imported.name),
        media_type=imported.media_type,
        storage_path=relative,
        size=len(imported.data),
        sha256=imported.sha256,
        meta=dict(imported.meta),
    )
    db.add(asset)
    db.flush()
    files.write(data_path(relative), imported.data)
    return asset


def safe_display_name(name: str) -> str:
    """Only for display: the last path component, without control characters."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(char for char in base if char.isprintable())[:500] or "source"


def primary_asset(db: Session, project: Project) -> SourceAsset | None:
    return db.scalar(
        select(SourceAsset)
        .where(SourceAsset.project_id == project.id, SourceAsset.format == "epub")
        .order_by(SourceAsset.created_at)
        .limit(1)
    )


def lock(db: Session, key: str) -> None:
    """Serializes imports of the same owner/content under PostgreSQL; SQLite writes one at a time."""
    if db.get_bind().dialect.name == "postgresql":
        digest = hashlib.sha256(key.encode()).digest()
        db.execute(select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], signed=True))))


def find_series(db: Session, owner_id: str, name: str) -> Series | None:
    return db.scalar(
        select(Series).where(Series.owner_id == owner_id, Series.normalized_name == normalize_series(name))
    )


def get_or_create_series(db: Session, owner_id: str, name: str, kind: str = "books", **values) -> Series:
    name = display_series(name)
    if not name:
        raise HTTPException(422, "Le nom de série est requis.")
    lock(db, f"series:{owner_id}:{normalize_series(name)}")
    series = find_series(db, owner_id, name)
    if series:
        return series
    series = Series(
        owner_id=owner_id,
        name=name,
        normalized_name=normalize_series(name),
        kind=kind,
        authors=[],
        bible={},
        **values,
    )
    db.add(series)
    db.flush()
    return series


def attach(project: Project, series: Series | None) -> None:
    project.series_id = series.id if series else None
    project.series_name = series.name if series else ""


def epub_duplicate(db: Session, owner_id: str, sha256: str) -> Project | None:
    return db.scalar(
        select(Project)
        .join(SourceAsset, SourceAsset.project_id == Project.id)
        .where(Project.owner_id == owner_id, SourceAsset.format == "epub", SourceAsset.sha256 == sha256)
        .limit(1)
    ) or db.scalar(select(Project).where(Project.owner_id == owner_id, Project.original_hash == sha256).limit(1))


def _add_chapter(
    db: Session, project: Project, chapter: ImportedChapter, position: int, segment_start: int,
    asset: SourceAsset | None,
) -> tuple[Chapter, int]:
    row = Chapter(
        project_id=project.id,
        position=position,
        title=chapter.title[:500] or "Sans titre",
        resource=chapter.resource,
        kind=chapter.kind,
        source_asset_id=asset.id if asset else None,
        external_id=chapter.external_id,
        chapter_number=chapter.number,
        source_checksum=chapter.checksum,
        import_meta=chapter.meta,
    )
    db.add(row)
    db.flush()
    for offset, group in enumerate(chapter.groups):
        db.add(
            Segment(
                project_id=project.id,
                chapter_id=row.id,
                position=segment_start + offset,
                units=group,
                source="\n\n".join(u["text"] for u in group),
                source_key=memory_key(group),
                section=group[0]["section"][:100],
            )
        )
    return row, len(chapter.groups)


def create_volume(
    db: Session,
    owner_id: str,
    volume: ImportedVolume,
    files: Files,
    *,
    series: Series | None = None,
    source_format: str,
    project_kind: str = "volume",
    number: int | None = None,
    title: str | None = None,
    external_id: str | None = None,
    meta: dict | None = None,
) -> Project:
    project_id = uid()
    project = Project(
        id=project_id,
        owner_id=owner_id,
        title=(title or volume.title or "Sans titre")[:500],
        author=volume.author[:500],
        source_language=(volume.language or "en")[:80],
        volume_number=number,
        source_format=source_format,
        project_kind=project_kind,
        external_id=external_id,
        book_info=volume.info,
        import_meta={"version": ADAPTER_VERSION, "adapter": source_format, **(meta or {})},
        original_hash=volume.asset.sha256 if volume.asset and source_format == "epub" else "",
        original_path=str(data_path(f"books/{project_id}.epub")) if source_format == "epub" else "",
    )
    if series:
        attach(project, series)
        for key in ("target_language", "provider_id", "quality", "context_backend"):
            if getattr(series, key):
                setattr(project, key, getattr(series, key))
        if series.source_language and source_format != "epub":
            project.source_language = series.source_language
    db.add(project)
    db.flush()
    asset = store_asset(db, project, volume.asset, files) if volume.asset else None
    position = 0
    for number_, chapter in enumerate(volume.chapters):
        chapter_asset = asset if chapter.asset is volume.asset else (
            store_asset(db, project, chapter.asset, files) if chapter.asset else None
        )
        _, count = _add_chapter(db, project, chapter, number_, position, chapter_asset)
        position += count
    if not position and project_kind != "serial":
        raise ValueError("Aucun texte traduisible trouvé dans la source.")
    return project


def serial_container(db: Session, series: Series, source_format: str = "txt") -> Project:
    lock(db, f"serial:{series.id}")
    existing = db.scalar(select(Project).where(Project.series_id == series.id, Project.project_kind == "serial"))
    if existing:
        return existing
    project = Project(
        owner_id=series.owner_id,
        title=series.name[:500],
        author=(series.authors or [""])[0][:500],
        source_language=series.source_language or "en",
        source_format=source_format,
        project_kind="serial",
        book_info={},
        import_meta={"version": ADAPTER_VERSION, "adapter": source_format},
        original_hash="",
        original_path="",
    )
    attach(project, series)
    for key in ("target_language", "provider_id", "quality", "context_backend"):
        if getattr(series, key):
            setattr(project, key, getattr(series, key))
    db.add(project)
    db.flush()
    return project


def text_resource(project: Project, fmt: str, key) -> str:
    """Virtual resource of a text chapter: stable for the same chapter of the same volume."""
    return f"{fmt}/{chapter_key(project.id, key)}"


def shift_positions(db: Session, project_id: str, start: int, delta: int) -> None:
    """Moves every passage from `start` on by `delta`, with everything that records its position.

    Memories, relations, first appearances and chapter summaries follow; external memory events are
    replayed with their new position (the old copies are no longer admitted: they differ from SQL).
    """
    if not delta:
        return
    moved = list(db.scalars(select(Segment.id).where(Segment.project_id == project_id, Segment.position >= start)))
    # Two steps: (project_id, position) is unique at every moment under PostgreSQL.
    for chunk in range(0, len(moved), 500):
        db.execute(
            update(Segment)
            .where(Segment.id.in_(moved[chunk : chunk + 500]))
            .values(position=-Segment.position - 1 - delta)
        )
    for chunk in range(0, len(moved), 500):
        db.execute(
            update(Segment).where(Segment.id.in_(moved[chunk : chunk + 500])).values(position=-Segment.position - 1)
        )
    db.execute(
        update(Memory).where(Memory.project_id == project_id, Memory.position >= start)
        .values(position=Memory.position + delta)
    )
    db.execute(
        update(CharacterRelation)
        .where(CharacterRelation.project_id == project_id, CharacterRelation.position >= start)
        .values(position=CharacterRelation.position + delta)
    )
    for model in (SeriesEntity, SeriesRelation):
        db.execute(
            update(model).where(model.first_project_id == project_id, model.first_position >= start)
            .values(first_position=model.first_position + delta)
        )
    for entity in db.scalars(select(Entity).where(Entity.project_id == project_id)):
        first = entity.data.get("first_position")
        if isinstance(first, int) and first >= start:
            entity.data = {**entity.data, "first_position": first + delta}
    for chapter in db.scalars(select(Chapter).where(Chapter.project_id == project_id)):
        through = (chapter.summary or {}).get("through_position")
        if isinstance(through, int) and through >= start:
            chapter.summary = {**chapter.summary, "through_position": through + delta}
    # Narrative states record the position they were computed through.
    for model, field_name in ((Segment, "narrative"), (Memory, "content")):
        for item in db.scalars(select(model).where(model.project_id == project_id)):
            value = getattr(item, field_name) or {}
            through = value.get("through_position") if isinstance(value, dict) else None
            if isinstance(through, int) and through >= start:
                setattr(item, field_name, {**value, "through_position": through + delta})
    for event in db.scalars(select(Outbox).where(Outbox.project_id == project_id)):
        position = event.payload.get("position")
        if isinstance(position, int) and position >= start:
            event.payload = {**event.payload, "position": position + delta}
            event.status, event.next_attempt, event.error = "pending", 0, ""


def require_idle(db: Session, project: Project) -> None:
    if db.scalar(select(Job.id).where(Job.project_id == project.id, Job.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(
            409, f"Un travail est en cours sur « {project.title} » : mettez-le en pause avant d’y ajouter des chapitres."
        )


@dataclass
class ChapterOutcome:
    chapter_id: str
    number: float | None
    title: str
    status: str  # created | unchanged | replaced
    external_id: str | None = None


def chapter_for(db: Session, project: Project, chapter: ImportedChapter) -> Chapter | None:
    if chapter.external_id:
        found = db.scalar(
            select(Chapter).where(Chapter.project_id == project.id, Chapter.external_id == chapter.external_id)
        )
        if found:
            return found
    if chapter.number is not None:
        return db.scalar(
            select(Chapter)
            .where(Chapter.project_id == project.id, Chapter.chapter_number == chapter.number)
            .order_by(Chapter.position)
            .limit(1)
        )
    return None


def conflicts(db: Session, project: Project, chapters: list[ImportedChapter], replace: set[int]) -> list[dict]:
    """Chapters already present with another content and not explicitly replaced."""
    found = []
    for index, chapter in enumerate(chapters):
        existing = chapter_for(db, project, chapter)
        if existing and existing.source_checksum != chapter.checksum and index not in replace:
            found.append(
                {"index": index, "chapter_id": existing.id, "number": existing.chapter_number, "title": existing.title}
            )
    return found


def add_chapters(
    db: Session,
    project: Project,
    chapters: list[ImportedChapter],
    files: Files,
    *,
    replace: set[int] = frozenset(),
    discard_human: bool = False,
) -> list[ChapterOutcome]:
    """Appends or inserts text chapters in number order; identical ones answer as unchanged.

    A chapter with the same number (or external identifier) and another content is refused unless
    its index is in `replace`: nothing already translated is overwritten silently.
    """
    if project.source_format == "epub":
        raise HTTPException(409, "Ce volume vient d’un EPUB : ajoutez les chapitres texte à un autre volume.")
    pending = conflicts(db, project, chapters, set(replace))
    if pending:
        raise HTTPException(
            409,
            {
                "message": "Des chapitres existent déjà avec un autre contenu : confirmez leur remplacement.",
                "conflicts": pending,
            },
        )
    outcomes = []
    changed = False
    for index, chapter in enumerate(chapters):
        existing = chapter_for(db, project, chapter)
        asset_needed = chapter.asset is not None
        if existing and existing.source_checksum == chapter.checksum:
            outcomes.append(ChapterOutcome(existing.id, existing.chapter_number, existing.title, "unchanged",
                                           existing.external_id))
            continue
        if not changed:
            require_idle(db, project)
            changed = True
        asset = store_asset(db, project, chapter.asset, files) if asset_needed else None
        if existing:
            replace_chapter(db, project, existing, chapter, asset, files, discard_human=discard_human)
            outcomes.append(ChapterOutcome(existing.id, existing.chapter_number, existing.title, "replaced",
                                           existing.external_id))
            continue
        row = insert_chapter(db, project, chapter, asset)
        outcomes.append(ChapterOutcome(row.id, row.chapter_number, row.title, "created", row.external_id))
    if changed:
        project.memory_revision += 1
        project.updated_at = time.time()
        db.flush()
        describe_text_volume(db, project)
        emit(db, project.id, status="imported", step="chapters", chapters=len(outcomes))
    return outcomes


def describe_text_volume(db: Session, project: Project) -> None:
    """The figures the library shows for a book (words, size, sources), computed for text volumes."""
    words = sum(len(plain(source).split()) for source in db.scalars(select(Segment.source).where(Segment.project_id == project.id)))
    size = db.scalar(select(func.coalesce(func.sum(SourceAsset.size), 0)).where(SourceAsset.project_id == project.id))
    chapters = db.scalar(select(func.count(Chapter.id)).where(Chapter.project_id == project.id))
    project.book_info = {**(project.book_info or {}), "words": words, "images": 0, "size": int(size or 0), "resources": chapters}


def insert_chapter(db: Session, project: Project, chapter: ImportedChapter, asset: SourceAsset | None) -> Chapter:
    ordered = list(db.scalars(select(Chapter).where(Chapter.project_id == project.id).order_by(Chapter.position)))
    after = [c for c in ordered if chapter.number is None or c.chapter_number is None or c.chapter_number < chapter.number]
    # Unnumbered chapters go to the end; numbered ones before the first chapter with a larger number.
    later = [c for c in ordered if c not in after] if chapter.number is not None else []
    position = (max((c.position for c in after), default=-1) + 1) if later else len(ordered)
    if later:
        first_later = min(later, key=lambda c: c.position)
        segment_start = db.scalar(
            select(func.min(Segment.position)).where(Segment.chapter_id == first_later.id)
        )
        if segment_start is None:
            segment_start = next_segment_position(db, project)
        db.execute(
            update(Chapter)
            .where(Chapter.project_id == project.id, Chapter.position >= position)
            .values(position=Chapter.position + 1)
        )
        shift_positions(db, project.id, segment_start, len(chapter.groups))
        for stale in later:
            stale.context_stale = True
    else:
        segment_start = next_segment_position(db, project)
    row, _ = _add_chapter(db, project, chapter, position, segment_start, asset)
    return row


def next_segment_position(db: Session, project: Project) -> int:
    last = db.scalar(select(func.max(Segment.position)).where(Segment.project_id == project.id))
    return 0 if last is None else last + 1


def replace_chapter(
    db: Session,
    project: Project,
    chapter: Chapter,
    imported: ImportedChapter,
    asset: SourceAsset | None,
    files: Files,
    *,
    discard_human: bool = False,
) -> None:
    """New source for one chapter. Passages whose text did not change keep their translation, human
    corrections included; only the others are reset. Later chapters are marked `context_stale`."""
    old = list(db.scalars(select(Segment).where(Segment.chapter_id == chapter.id).order_by(Segment.position)))
    available: dict[str, list[Segment]] = {}
    for segment in old:
        available.setdefault(segment.source_key or "", []).append(segment)
    plan: list[Segment | None] = []
    for group in imported.groups:
        candidates = available.get(memory_key(group)) or []
        plan.append(candidates.pop(0) if candidates else None)
    kept = {segment.id for segment in plan if segment is not None}
    removed = [segment for segment in old if segment.id not in kept]
    protected = [segment for segment in removed if segment.human or segment.validated]
    if protected and not discard_human:
        raise HTTPException(
            409,
            {
                "message": "Ce remplacement supprimerait des passages corrigés ou validés par une personne. "
                "Confirmez explicitement leur abandon.",
                "protected_segments": [segment.id for segment in protected],
            },
        )
    start = old[0].position if old else next_segment_position(db, project)
    if removed:
        ids = [segment.id for segment in removed]
        # Their model calls were paid: the statistics keep them without the passage.
        db.execute(update(RequestLog).where(RequestLog.segment_id.in_(ids)).values(segment_id=None))
        memory_ids = list(db.scalars(select(Memory.id).where(Memory.segment_id.in_(ids))))
        if memory_ids:
            db.execute(Outbox.__table__.delete().where(Outbox.event_key.in_(memory_ids)))
        for segment in removed:
            db.delete(segment)
        db.flush()
    # Park the chapter's remaining passages, move the rest of the book, then lay the chapter out again.
    for offset, segment in enumerate(segment for segment in plan if segment is not None):
        segment.position = -1 - offset
    db.flush()
    shift_positions(db, project.id, start + len(old), len(imported.groups) - len(old))
    relocated: dict[str, int] = {}
    for offset, (group, segment) in enumerate(zip(imported.groups, plan, strict=True)):
        position = start + offset
        if segment is not None:
            relocated[segment.id] = position
        if segment is None:
            db.add(
                Segment(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    position=position,
                    units=group,
                    source="\n\n".join(u["text"] for u in group),
                    source_key=memory_key(group),
                    section=group[0]["section"][:100],
                )
            )
            continue
        _relocate(db, segment, group, position)
    for event in db.scalars(select(Outbox).where(Outbox.project_id == project.id)):
        position = relocated.get(event.payload.get("segment_id"))
        if position is not None and event.payload.get("position") != position:
            event.payload = {**event.payload, "position": position}
            event.status, event.next_attempt, event.error = "pending", 0, ""
    previous_asset = chapter.source_asset_id
    chapter.source_asset_id = asset.id if asset else chapter.source_asset_id
    chapter.source_checksum = imported.checksum
    chapter.import_meta = imported.meta
    chapter.analyzed, chapter.summary, chapter.context_stale = False, {}, False
    if imported.title and imported.title != chapter.title and not chapter.external_id:
        chapter.title = imported.title[:500]
    db.execute(
        update(Chapter)
        .where(Chapter.project_id == project.id, Chapter.position > chapter.position)
        .values(context_stale=True)
    )
    if asset and previous_asset and previous_asset != asset.id:
        old_asset = db.get(SourceAsset, previous_asset)
        still_used = db.scalar(
            select(Chapter.id).where(Chapter.source_asset_id == previous_asset, Chapter.id != chapter.id).limit(1)
        )
        if old_asset and not still_used:
            path = asset_file(old_asset)
            if path:
                files.obsolete.append(path)
            db.delete(old_asset)
    db.flush()


def _relocate(db: Session, segment: Segment, group: list[dict], position: int) -> None:
    """A passage with the same text keeps its work; unit identifiers follow the new layout."""
    ids = {old["id"]: new["id"] for old, new in zip(segment.units, group, strict=True)}
    segment.position = position
    db.execute(update(Memory).where(Memory.segment_id == segment.id).values(position=position))
    db.execute(update(CharacterRelation).where(CharacterRelation.segment_id == segment.id).values(position=position))
    segment.units = group
    if any(old != new for old, new in ids.items()):
        segment.translated_units = [dict(unit, id=ids.get(unit["id"], unit["id"])) for unit in segment.translated_units]
        for version in db.scalars(select(TranslationVersion).where(TranslationVersion.segment_id == segment.id)):
            version.units = [dict(unit, id=ids.get(unit["id"], unit["id"])) for unit in version.units]
        segment.revision += 1
    segment.section = group[0]["section"][:100]
