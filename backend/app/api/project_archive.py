"""Project archive: everything a book's work is made of, in a versioned and validated format.

Version 2 carries the whole state of the translation: passage statuses, critiques, uncertainties,
version history, bible revisions, quality issues, jobs with their per-passage state and request
statistics. Version 1 archives
are still read. Owners, members, permissions and the provider are never restored: the person who
restores becomes the owner, shares the book again and chooses a provider of this server.
"""

import io
import json
import time
import zipfile
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app import __version__
from app.api.common import row
from app.config import settings
from app.engines.epub.archive import safe_name
from app.engines.memory.archive import restore_graph
from app.engines.quality.checks import validate_translation
from app.jobs.queue import HELD
from app.jobs.segment_state import split_legacy
from app.models import (
    BibleRevision,
    Chapter,
    CharacterRelation,
    Entity,
    EntityMerge,
    Glossary,
    Issue,
    Job,
    JobSegmentState,
    Memory,
    Project,
    RequestLog,
    Segment,
    TranslationVersion,
)
from app.models.common import uid
from app.schemas import BookBible, GlossaryInput, TranslationResult

SCHEMA_VERSION = 2

# Columns deliberately left out of the archive. Everything else is exported and restored; a test
# fails when a new column is neither restored nor listed here.
NOT_ARCHIVED = {
    Project: {"id", "owner_id", "provider_id", "original_path", "original_hash", "archived_at", "updated_at"},
    Chapter: {"project_id"},
    # The structure comes from the EPUB itself; `translation` and `source_key` are derived from the units.
    Segment: {"project_id", "chapter_id", "section", "units", "translation", "source_key"},
    TranslationVersion: {"id", "author_id"},
    Glossary: {"id", "project_id"},
    Entity: {"project_id"},
    CharacterRelation: {"id", "project_id"},
    EntityMerge: {"id", "project_id"},
    Memory: {"id", "project_id"},
    BibleRevision: {"id", "project_id"},
    Issue: {"id", "project_id"},
    Job: {"project_id", "provider_id", "lease_owner", "lease_until", "next_attempt"},
    JobSegmentState: set(),
    # Prompts and answers stay on the server that paid for them; the figures travel.
    RequestLog: {
        "id", "project_id", "provider_id", "execution_owner", "fingerprint",
        "parameters", "messages", "context", "raw", "parsed",
    },
}  # fmt: skip


class Archived(BaseModel):
    model_config = ConfigDict(extra="ignore")
    created_at: float | None = None


class ArchivedProject(Archived):
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=500)
    series_name: str = Field(default="", max_length=500)
    volume_number: int | None = Field(default=None, ge=1, le=10000)
    source_language: str | None = Field(default=None, max_length=80)
    target_language: str = Field(default="fr", max_length=80)
    quality: Literal["fast", "normal", "high", "maximum"] = "normal"
    context_backend: Literal["internal", "openviking", "hybrid"] = "internal"
    status: str = Field(default="pending", max_length=30)
    book_info: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    instructions: str = Field(default="", max_length=20000)
    bible: dict = Field(default_factory=dict)
    bible_validated: bool = False
    memory_revision: int = Field(default=0, ge=0)


class ArchivedChapter(Archived):
    id: str
    position: int
    resource: str
    title: str | None = Field(default=None, max_length=500)
    summary: dict = Field(default_factory=dict)
    instructions: str = Field(default="", max_length=10000)
    analyzed: bool = False
    kind: Literal["narrative", "auxiliary", "navigation", "metadata"] | None = None


class ArchivedSegment(Archived):
    id: str
    position: int
    source: str
    translated_units: list[dict] = Field(default_factory=list)
    status: str = Field(default="pending", max_length=30)
    stage: str = Field(default="pending", max_length=30)
    human: bool = False
    retained_source: bool = False
    validated: bool = False
    revision: int = Field(default=0, ge=0)
    instructions: str = Field(default="", max_length=10000)
    uncertainties: list = Field(default_factory=list)
    critique: list = Field(default_factory=list)
    narrative: dict = Field(default_factory=dict)
    error: str = ""


class ArchivedVersion(Archived):
    segment_id: str
    units: list[dict]
    origin: str = Field(max_length=30)
    base_revision: int = Field(default=0, ge=0)
    applied: bool = False


class ArchivedTerm(GlossaryInput):
    model_config = ConfigDict(extra="ignore")
    created_at: float | None = None


class ArchivedEntity(Archived):
    id: str
    name: str = Field(max_length=300)
    category: str = Field(max_length=50)
    data: dict
    validated: bool = False
    identity_validated: bool = False
    merged_into_id: str | None = None


class ArchivedRelation(Archived):
    source_id: str
    target_id: str
    segment_id: str | None = None
    position: int = -1
    relation_type: str = Field(max_length=80)
    description: str = ""
    evidence: str = ""
    provenance: str = Field(default="import", max_length=30)
    validated: bool = False
    active: bool = True


class ArchivedMerge(Archived):
    target_id: str
    source_ids: list[str]
    snapshots: list
    reason: str = "Import"
    human: bool = False


class ArchivedMemory(Archived):
    segment_id: str | None = None
    position: int = -1
    kind: str = Field(max_length=30)
    content: dict
    validated: bool = False


class ArchivedBibleRevision(Archived):
    content: dict
    human: bool = False


class ArchivedIssue(Archived):
    segment_id: str | None = None
    severity: str = Field(max_length=20)
    code: str = Field(max_length=50)
    message: str
    resolved: bool = False


class ArchivedJob(Archived):
    id: str
    operation: str = Field(max_length=40)
    status: str = Field(max_length=30)
    options: dict = Field(default_factory=dict)
    checkpoint: dict = Field(default_factory=dict)
    attempts: int = Field(default=0, ge=0)
    error: str = ""
    outage_count: int = Field(default=0, ge=0)
    stop_reason: str = Field(default="", max_length=40)
    finished_at: float | None = None


class ArchivedJobState(BaseModel):
    """What a job had settled per passage: a restored paused job resumes without redoing it, and the
    book keeps its final review history."""

    model_config = ConfigDict(extra="ignore")
    job_id: str
    step: str = Field(max_length=30)
    segment_id: str = Field(default="", max_length=36)
    key: str = Field(default="", max_length=100)
    outcome: str = Field(default="", max_length=30)
    data: dict = Field(default_factory=dict)


class ArchivedRequest(Archived):
    job_id: str | None = None
    segment_id: str | None = None
    operation: str = Field(max_length=50)
    model: str = Field(default="", max_length=200)
    status: str = Field(default="success", max_length=30)
    duration: float = 0
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    attempt: int = Field(default=1, ge=0)
    cached: bool = False
    input_cost: float | None = None
    output_cost: float | None = None
    error: str = ""


class ProjectArchive(BaseModel):
    model_config = ConfigDict(extra="ignore")
    schema_version: Literal[1, 2]
    project: ArchivedProject
    chapters: list[ArchivedChapter] = Field(default_factory=list)
    segments: list[ArchivedSegment] = Field(default_factory=list)
    versions: list[ArchivedVersion] = Field(default_factory=list)
    glossary: list[ArchivedTerm] = Field(default_factory=list)
    entities: list[ArchivedEntity] = Field(default_factory=list)
    character_relations: list[ArchivedRelation] = Field(default_factory=list)
    entity_merges: list[ArchivedMerge] = Field(default_factory=list)
    memories: list[ArchivedMemory] = Field(default_factory=list)
    bible_revisions: list[ArchivedBibleRevision] = Field(default_factory=list)
    issues: list[ArchivedIssue] = Field(default_factory=list)
    jobs: list[ArchivedJob] = Field(default_factory=list)
    job_state: list[ArchivedJobState] = Field(default_factory=list)
    requests: list[ArchivedRequest] = Field(default_factory=list)


ARCHIVED_FIELDS = {
    Project: ArchivedProject,
    Chapter: ArchivedChapter,
    Segment: ArchivedSegment,
    TranslationVersion: ArchivedVersion,
    Glossary: ArchivedTerm,
    Entity: ArchivedEntity,
    CharacterRelation: ArchivedRelation,
    EntityMerge: ArchivedMerge,
    Memory: ArchivedMemory,
    BibleRevision: ArchivedBibleRevision,
    Issue: ArchivedIssue,
    Job: ArchivedJob,
    JobSegmentState: ArchivedJobState,
    RequestLog: ArchivedRequest,
}


def _rows(db, model, *conditions, order=None) -> list[dict]:
    query = select(model).where(*conditions).order_by(order if order is not None else model.created_at)
    return [row(item, tuple(NOT_ARCHIVED[model])) for item in db.scalars(query)]


def _limit_message(size: int, limit_mb: int, setting: str) -> str:
    return (
        f"Archive de projet trop volumineuse pour être réimportée : {size / 1024**2:.1f} Mo pour "
        f"{limit_mb} Mo autorisés ({setting}). Augmentez ce réglage sur les serveurs d’export et de "
        "restauration, ou purgez l’historique des requêtes de ce livre."
    )


def build_archive(db, project: Project, original: bytes) -> bytes:
    pid = project.id
    payload = {
        "schema_version": SCHEMA_VERSION,
        "libris_version": __version__,
        "exported_at": time.time(),
        "project": row(project, tuple(NOT_ARCHIVED[Project])),
        "chapters": _rows(db, Chapter, Chapter.project_id == pid, order=Chapter.position),
        "segments": _rows(db, Segment, Segment.project_id == pid, order=Segment.position),
        "versions": _rows(
            db,
            TranslationVersion,
            TranslationVersion.segment_id.in_(select(Segment.id).where(Segment.project_id == pid)),
        ),
        "glossary": _rows(db, Glossary, Glossary.project_id == pid),
        "entities": _rows(db, Entity, Entity.project_id == pid),
        "character_relations": _rows(db, CharacterRelation, CharacterRelation.project_id == pid),
        "entity_merges": _rows(db, EntityMerge, EntityMerge.project_id == pid),
        "memories": _rows(db, Memory, Memory.project_id == pid),
        "bible_revisions": _rows(db, BibleRevision, BibleRevision.project_id == pid),
        "issues": _rows(db, Issue, Issue.project_id == pid),
        "jobs": _rows(db, Job, Job.project_id == pid),
        "job_state": [
            row(item)
            for item in db.scalars(
                select(JobSegmentState).where(JobSegmentState.job_id.in_(select(Job.id).where(Job.project_id == pid)))
            )
        ],
        "requests": _rows(db, RequestLog, RequestLog.project_id == pid),
    }
    document = json.dumps(payload, ensure_ascii=False).encode()
    limits = settings()
    unpacked = len(original) + len(document)
    if unpacked > limits.max_unpacked_mb * 1024**2:
        raise HTTPException(413, _limit_message(unpacked, limits.max_unpacked_mb, "MAX_UNPACKED_MB"))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("original.epub", original)
        archive.writestr("project.json", document)
    if output.tell() > limits.max_upload_mb * 1024**2:
        raise HTTPException(413, _limit_message(output.tell(), limits.max_upload_mb, "MAX_UPLOAD_MB"))
    return output.getvalue()


def read_archive(data: bytes) -> tuple[bytes, ProjectArchive]:
    limits = settings()
    if len(data) > limits.max_upload_mb * 1024**2:
        raise HTTPException(413, "Archive projet trop volumineuse.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) != 2 or {safe_name(i.filename) for i in infos} != {"original.epub", "project.json"}:
            raise ValueError("Archive projet invalide.")
        if sum(i.file_size for i in infos) > limits.max_unpacked_mb * 1024**2:
            raise ValueError("Archive projet trop volumineuse après décompression.")
        original = archive.read("original.epub")
        document = archive.read("project.json")
    try:
        payload = json.loads(document)
    except ValueError:
        raise HTTPException(422, "Archive de projet invalide : project.json n’est pas un JSON lisible.") from None
    if not isinstance(payload, dict) or payload.get("schema_version") not in {1, 2}:
        raise HTTPException(422, "Version de projet non prise en charge.")
    try:
        return original, ProjectArchive.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, invalid_archive_message(exc)) from None


def invalid_archive_message(exc: ValidationError) -> str:
    problems = [
        f"{'.'.join(str(part) for part in error['loc'])} ({error['type']})" for error in exc.errors()[:5]
    ]
    more = f" et {exc.error_count() - 5} autre(s)" if exc.error_count() > 5 else ""
    return "Archive de projet invalide : champ(s) incorrect(s) " + ", ".join(problems) + more + "."


def remap(value, ids: dict[str, str]):
    """Point identifiers saved in free-form JSON (checkpoints, snapshots) at the restored rows."""
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [remap(item, ids) for item in value]
    if isinstance(value, dict):
        return {ids.get(key, key): remap(item, ids) for key, item in value.items()}
    return value


def _dated(values: dict) -> dict:
    return {"created_at": values["created_at"]} if values.get("created_at") is not None else {}


def restore_archive(db, project: Project, archive: ProjectArchive) -> None:
    """Fill a freshly imported book (new identifiers, restoring user as owner) from its archive."""
    info = archive.project
    for key in ("title", "author", "source_language"):
        if getattr(info, key):
            setattr(project, key, getattr(info, key))
    project.series_name = info.series_name
    project.volume_number = info.volume_number
    project.target_language = info.target_language
    project.quality = info.quality
    project.context_backend = info.context_backend
    project.config = info.config
    project.instructions = info.instructions
    # An empty bible means "not analysed yet": translation stays locked until the analysis runs.
    if info.bible:
        BookBible.model_validate(info.bible)
    project.bible = info.bible
    project.bible_validated = info.bible_validated
    project.memory_revision = info.memory_revision
    # A book whose work was running waits for Resume; "pending" is also the status of a fresh import.
    project.status = "paused" if info.status in HELD and info.status != "pending" else info.status
    if info.created_at:
        project.created_at = info.created_at
    if "validation" in info.book_info and "validation" not in project.book_info:
        project.book_info = dict(project.book_info, validation=info.book_info["validation"])

    ids: dict[str, str] = {}
    chapters = {
        (chapter.position, chapter.resource): chapter
        for chapter in db.scalars(select(Chapter).where(Chapter.project_id == project.id))
    }
    for saved in archive.chapters:
        chapter = chapters.get((saved.position, saved.resource))
        if not chapter:
            raise ValueError("Structure du projet incompatible avec son EPUB original.")
        ids[saved.id] = chapter.id
        if saved.title:
            chapter.title = saved.title
        chapter.summary = saved.summary
        chapter.instructions = saved.instructions
        chapter.analyzed = saved.analyzed
        # Archives written before chapter kinds keep the kind the fresh import derived from the EPUB.
        if saved.kind:
            chapter.kind = saved.kind
        if saved.created_at:
            chapter.created_at = saved.created_at

    current = list(
        db.scalars(select(Segment).where(Segment.project_id == project.id).order_by(Segment.position))
    )
    if len(current) != len(archive.segments):
        raise ValueError("Structure du projet incompatible avec son EPUB original.")
    for segment, saved in zip(current, sorted(archive.segments, key=lambda s: s.position), strict=True):
        if segment.source != saved.source:
            raise ValueError("Le texte source du projet ne correspond pas à l’EPUB.")
        ids[saved.id] = segment.id
        if saved.translated_units:
            units = [u.model_dump() for u in TranslationResult(units=saved.translated_units).units]
            validate_translation(segment.units, TranslationResult(units=units))
            segment.translated_units = units
            segment.translation = "\n\n".join(u["text"] for u in units)
        if saved.created_at:
            segment.created_at = saved.created_at
        for key in (
            "status", "stage", "human", "retained_source", "validated", "revision", "instructions",
            "uncertainties", "critique", "narrative", "error",
        ):  # fmt: skip
            setattr(segment, key, getattr(saved, key))

    for version in archive.versions:
        if version.segment_id not in ids:
            raise ValueError("Version rattachée à un passage absent de l’archive.")
        db.add(
            TranslationVersion(
                segment_id=ids[version.segment_id],
                units=version.units,
                origin=version.origin,
                base_revision=version.base_revision,
                applied=version.applied,
                **_dated(version.model_dump()),
            )
        )
    for term in archive.glossary:
        db.add(Glossary(project_id=project.id, **term.model_dump(exclude_none=True)))
    entity_ids = restore_graph(
        db,
        project.id,
        {
            "entities": [item.model_dump() for item in archive.entities],
            "character_relations": [item.model_dump() for item in archive.character_relations],
            "entity_merges": [item.model_dump() for item in archive.entity_merges],
        },
        ids,
    )
    ids.update(entity_ids)
    for merge in db.scalars(select(EntityMerge).where(EntityMerge.project_id == project.id)):
        merge.snapshots = remap(merge.snapshots, ids)
    for memory in archive.memories:
        if memory.segment_id is not None and memory.segment_id not in ids:
            continue
        db.add(
            Memory(
                project_id=project.id,
                segment_id=ids.get(memory.segment_id),
                position=memory.position,
                kind=memory.kind,
                content=remap(memory.content, ids),
                validated=memory.validated,
                **_dated(memory.model_dump()),
            )
        )
    for revision in archive.bible_revisions:
        db.add(BibleRevision(project_id=project.id, content=revision.content, human=revision.human,
                             **_dated(revision.model_dump())))
    for issue in archive.issues:
        db.add(
            Issue(
                project_id=project.id,
                segment_id=ids.get(issue.segment_id),
                **issue.model_dump(exclude={"segment_id", "created_at"}),
                **_dated(issue.model_dump()),
            )
        )
    restore_jobs(db, project, archive, ids)


def restore_jobs(db, project: Project, archive: ProjectArchive, ids: dict[str, str]) -> None:
    ids.update({saved.id: uid() for saved in archive.jobs})
    states: dict[tuple, dict] = {}
    for saved in archive.job_state:
        states[(saved.job_id, saved.step, saved.segment_id, saved.key)] = saved.model_dump()
    for saved in archive.jobs:
        # Archives exported before v0.5 keep this state as lists inside the checkpoint.
        checkpoint, legacy = split_legacy(saved.checkpoint)
        for item in legacy:
            states.setdefault((saved.id, item["step"], item["segment_id"], item["key"]), {**item, "job_id": saved.id})
        status, stop_reason = saved.status, saved.stop_reason
        if status in HELD and status != "paused":
            # Nothing starts by itself after a restore: unfinished work waits for Resume.
            status, stop_reason = "paused", "project_restored"
        db.add(
            Job(
                id=ids[saved.id],
                project_id=project.id,
                operation=saved.operation,
                status=status,
                stop_reason=stop_reason,
                # The provider is not restored: a job resumes on the one chosen for the book here.
                options=remap({k: v for k, v in saved.options.items() if k != "provider_id"}, ids),
                checkpoint=remap(checkpoint, ids),
                attempts=saved.attempts,
                error=saved.error,
                outage_count=saved.outage_count,
                finished_at=saved.finished_at,
                **_dated(saved.model_dump()),
            )
        )
    db.flush()
    for item in states.values():
        # Batch rows have no passage; a passage absent from this book is not carried over.
        if item["job_id"] not in ids or (item["segment_id"] and item["segment_id"] not in ids):
            continue
        db.add(
            JobSegmentState(
                **item | {"job_id": ids[item["job_id"]], "segment_id": ids.get(item["segment_id"], "")}
            )
        )
    for saved in archive.requests:
        values = saved.model_dump(exclude={"job_id", "segment_id", "created_at"})
        if values["status"] == "running":
            values["status"] = "error"
        db.add(
            RequestLog(
                project_id=project.id,
                job_id=ids.get(saved.job_id) if saved.job_id else None,
                segment_id=ids.get(saved.segment_id) if saved.segment_id else None,
                fingerprint="",
                parameters={},
                messages=[],
                **values,
                **_dated(saved.model_dump()),
            )
        )

