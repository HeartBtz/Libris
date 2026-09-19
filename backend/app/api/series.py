"""Series: the library's first level, their volumes and chapters, and the series memory.

Only the owner changes a series. A person a volume is shared with sees that series with the volumes
they can read, and nothing else of it.
"""

import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy import func, or_, select

from app.api.common import row
from app.api.projects import project_views
from app.engines.context.config import memory_config
from app.engines.ingestion.naming import display_series, missing_numbers, normalize_series
from app.engines.ingestion.store import find_series
from app.engines.memory.catalog import queue_catalog
from app.engines.memory.cleanup import queue_series_cleanup
from app.engines.memory.events import ensure_events
from app.engines.series.audit import audit
from app.engines.series.bible import canonical, refresh_series, series_volumes
from app.i18n import localize, preferred_language
from app.jobs.queue import HELD, RUNNING
from app.models import (
    AuditEntry,
    Chapter,
    Entity,
    Glossary,
    Job,
    Membership,
    Outbox,
    Project,
    Provider,
    Segment,
    Series,
    SeriesEntity,
    SeriesEntityLink,
    SeriesRelation,
    SeriesTerm,
    User,
)
from app.progress import book_facts, books_progress
from app.providers.openviking import OpenVikingClient, series_uri
from app.schemas import StrictModel
from app.security import DB, CurrentUser

router = APIRouter(prefix="/api/series")


def readable_projects(db, user: User, include_archived: bool = True):
    member = select(Membership.project_id).where(Membership.user_id == user.id)
    query = select(Project).where(or_(Project.owner_id == user.id, Project.id.in_(member)))
    if not include_archived:
        query = query.where(Project.archived_at.is_(None))
    return query


def series_access(db, series_id: str, user: User, owner: bool = False) -> Series:
    series = db.get(Series, series_id)
    if not series:
        raise HTTPException(404, "Série introuvable.")
    if series.owner_id == user.id:
        return series
    shared = db.scalar(
        readable_projects(db, user).where(Project.series_id == series_id).with_only_columns(Project.id).limit(1)
    )
    if owner or not shared:
        raise HTTPException(404, "Série introuvable ou accès insuffisant.")
    return series


def series_views(db, user: User, series: list[Series], include_archived: bool = False) -> list[dict]:
    if not series:
        return []
    ids = [s.id for s in series]
    query = readable_projects(db, user, include_archived).where(Project.series_id.in_(ids))
    projects = list(db.scalars(query))
    facts = book_facts(db, [p.id for p in projects])
    progress = books_progress(db, projects, facts)
    providers = {p.id: p for p in db.scalars(select(Provider).where(Provider.id.in_({p.provider_id for p in projects} - {None})))}
    outbox = {
        pid: (pending, failed)
        for pid, pending, failed in db.execute(
            select(
                Outbox.project_id,
                func.count().filter(Outbox.status != "sent"),
                func.count().filter(Outbox.status == "error"),
            )
            .where(Outbox.project_id.in_([p.id for p in projects]))
            .group_by(Outbox.project_id)
        )
    }
    views = []
    for current in series:
        volumes = [p for p in projects if p.series_id == current.id]
        stats = [facts[p.id].stats for p in volumes]
        total = sum(s.get("total", 0) for s in stats)
        translated = sum(s.get("translated", 0) for s in stats)
        numbers = [p.volume_number for p in volumes if p.volume_number is not None]
        backends = sorted({p.context_backend for p in volumes})
        running = [progress[p.id] for p in volumes if progress[p.id]["state"] in RUNNING]
        views.append(
            {
                **row(current, ("bible",)),
                "shared": current.owner_id != user.id,
                "volumes": sum(1 for p in volumes if p.project_kind == "volume"),
                "serial": any(p.project_kind == "serial" for p in volumes),
                "chapters": sum(s.get("chapters", 0) for s in stats),
                "formats": sorted({p.source_format for p in volumes}),
                "progress": {
                    "total": total,
                    "translated": translated,
                    "validated": sum(s.get("validated", 0) for s in stats),
                    "percent": round(translated / total * 100) if total else 0,
                    "running": len(running),
                },
                "issues": {
                    "flagged": sum(s.get("flagged", 0) for s in stats),
                    "errors": sum(s.get("errors", 0) + s.get("refused", 0) for s in stats),
                    "context_stale": 0,
                },
                "activity": max([current.updated_at, *(p.updated_at for p in volumes)]),
                "providers": [
                    {"id": providers[pid].id, "name": providers[pid].name, "model": providers[pid].model}
                    for pid in sorted({p.provider_id for p in volumes} - {None})
                    if pid in providers
                ],
                "memory": {
                    "backends": backends,
                    "pending": sum(outbox.get(p.id, (0, 0))[0] for p in volumes),
                    "failed": sum(outbox.get(p.id, (0, 0))[1] for p in volumes),
                },
                "missing_volumes": missing_numbers(numbers),
                "duplicate_volumes": sorted({n for n in numbers if numbers.count(n) > 1}),
            }
        )
    stale = dict(
        db.execute(
            select(Project.series_id, func.count(Chapter.id))
            .join(Chapter, Chapter.project_id == Project.id)
            .where(Project.series_id.in_(ids), Chapter.context_stale.is_(True))
            .group_by(Project.series_id)
        ).all()
    )
    for view in views:
        view["issues"]["context_stale"] = stale.get(view["id"], 0)
    return views


@router.get("")
def list_series(user: CurrentUser, db: DB, include_archived: bool = False):
    shared_ids = select(Project.series_id).where(
        Project.id.in_(select(Membership.project_id).where(Membership.user_id == user.id))
    )
    query = select(Series).where(or_(Series.owner_id == user.id, Series.id.in_(shared_ids)))
    if not include_archived:
        query = query.where(Series.archived_at.is_(None))
    series = list(db.scalars(query.order_by(Series.name)))
    return series_views(db, user, series, include_archived)


class SeriesInput(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    kind: Literal["books", "webnovel"] = "books"
    source_language: str | None = Field(default=None, min_length=2, max_length=80)
    target_language: str | None = Field(default=None, min_length=2, max_length=80)
    provider_id: str | None = None
    quality: Literal["fast", "normal", "high", "maximum"] | None = None
    context_backend: Literal["internal", "openviking", "hybrid"] | None = None
    instructions: str = Field(default="", max_length=20000)


def apply_series(db, series: Series, body: SeriesInput) -> None:
    if body.provider_id and not db.get(Provider, body.provider_id):
        raise HTTPException(422, "Provider inconnu.")
    for key in ("kind", "source_language", "target_language", "provider_id", "quality", "context_backend", "instructions"):
        setattr(series, key, getattr(body, key))


@router.post("", status_code=201)
def create_series(body: SeriesInput, user: CurrentUser, db: DB):
    name = display_series(body.name)
    existing = find_series(db, user.id, name)
    if existing:
        raise HTTPException(409, {"message": f"Une série « {existing.name} » existe déjà.", "series_id": existing.id})
    series = Series(owner_id=user.id, name=name, normalized_name=normalize_series(name), authors=[], bible={})
    apply_series(db, series, body)
    db.add(series)
    db.commit()
    return series_views(db, user, [series])[0]


@router.get("/{series_id}")
def get_series(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user)
    view = series_views(db, user, [series], include_archived=True)[0]
    volumes = [
        p for p in series_volumes(db, series.id)
        if p.owner_id == user.id or db.get(Membership, (p.id, user.id))
    ]
    return {**view, "bible": series.bible, "volume_list": project_views(db, volumes, bible=False)}


@router.put("/{series_id}")
def update_series(series_id: str, body: SeriesInput, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    name = display_series(body.name)
    other = find_series(db, user.id, name)
    if other and other.id != series.id:
        raise HTTPException(409, {"message": f"Une série « {other.name} » existe déjà.", "series_id": other.id})
    if body.kind != series.kind and series.kind == "webnovel" and db.scalar(
        select(Project.id).where(Project.series_id == series.id, Project.project_kind == "serial").limit(1)
    ):
        raise HTTPException(409, "Cette série contient un flux continu de chapitres : elle reste une webnovel.")
    apply_series(db, series, body)
    if name != series.name:
        series.name, series.normalized_name = name, normalize_series(name)
        for project in db.scalars(select(Project).where(Project.series_id == series.id)):
            project.series_name = name
    db.commit()
    return series_views(db, user, [series], include_archived=True)[0]


@router.delete("/{series_id}")
def delete_series(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    if db.scalar(select(Project.id).where(Project.series_id == series.id).limit(1)):
        raise HTTPException(409, "Supprimez ou détachez d’abord les volumes de cette série.")
    cleanup = queue_series_cleanup(db, series, user.id)
    db.delete(series)
    db.commit()
    return {
        "ok": True,
        "message": "Série supprimée. Ses documents OpenViking seront effacés par le worker."
        if cleanup
        else "Série supprimée.",
        "openviking_cleanup_id": cleanup.id if cleanup else None,
    }


@router.post("/{series_id}/archive")
def archive_series(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    volumes = list(db.scalars(select(Project).where(Project.series_id == series.id)))
    if db.scalar(select(Job.id).where(Job.project_id.in_([p.id for p in volumes]), Job.status.in_(HELD)).limit(1)):
        raise HTTPException(409, "Terminez ou annulez le travail des volumes avant d’archiver cette série.")
    now = time.time()
    series.archived_at = series.archived_at or now
    for project in volumes:
        project.archived_at = project.archived_at or now
    db.commit()
    return series_views(db, user, [series], include_archived=True)[0]


@router.post("/{series_id}/restore")
def restore_series(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    archived_with = series.archived_at
    series.archived_at = None
    for project in db.scalars(select(Project).where(Project.series_id == series.id)):
        # Volumes archived on their own before the series keep their state.
        if project.archived_at is not None and archived_with and project.archived_at >= archived_with:
            project.archived_at = None
    db.commit()
    return series_views(db, user, [series], include_archived=True)[0]


class VolumeNumber(StrictModel):
    project_id: str
    volume_number: int | None = Field(default=None, ge=1, le=10000)


class VolumesInput(StrictModel):
    items: list[VolumeNumber] = Field(min_length=1, max_length=2000)


@router.put("/{series_id}/volumes")
def number_volumes(series_id: str, body: VolumesInput, user: CurrentUser, db: DB):
    """Renumbers or reorders volumes at once; two volumes never end with the same number."""
    series = series_access(db, series_id, user, owner=True)
    volumes = {p.id: p for p in db.scalars(select(Project).where(Project.series_id == series.id))}
    for item in body.items:
        if item.project_id not in volumes:
            raise HTTPException(404, "Volume introuvable dans cette série.")
        if volumes[item.project_id].project_kind == "serial" and item.volume_number is not None:
            raise HTTPException(422, "Le flux continu de chapitres n’a pas de numéro de volume.")
    final = {pid: p.volume_number for pid, p in volumes.items()}
    final.update({item.project_id: item.volume_number for item in body.items})
    numbers = [n for n in final.values() if n is not None]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise HTTPException(422, f"Numéros de volume en double : {', '.join(map(str, duplicates))}.")
    for item in body.items:
        volumes[item.project_id].volume_number = item.volume_number
    refresh_series(db, series.id)
    db.commit()
    return project_views(db, series_volumes(db, series.id), bible=False)


class AttachInput(StrictModel):
    project_id: str
    volume_number: int | None = Field(default=None, ge=1, le=10000)


@router.post("/{series_id}/volumes")
def attach_volume(series_id: str, body: AttachInput, user: CurrentUser, db: DB):
    """A standalone volume (or a volume of another series of the owner) joins this series."""
    series = series_access(db, series_id, user, owner=True)
    project = db.get(Project, body.project_id)
    if not project or project.owner_id != user.id:
        raise HTTPException(404, "Projet introuvable.")
    if project.project_kind == "serial":
        raise HTTPException(409, "Les chapitres d’une webnovel appartiennent obligatoirement à leur série.")
    if body.volume_number is not None and db.scalar(
        select(Project.id).where(
            Project.series_id == series.id, Project.volume_number == body.volume_number, Project.id != project.id
        )
    ):
        raise HTTPException(409, f"Le volume {body.volume_number} existe déjà dans cette série.")
    previous = project.series_id
    project.series_id, project.volume_number = series.id, body.volume_number
    db.flush()
    for touched in {previous, series.id} - {None}:
        refresh_series(db, touched)
    db.commit()
    return project_views(db, [project], bible=False)[0]


@router.post("/{series_id}/volumes/{project_id}/detach")
def detach_volume(series_id: str, project_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    project = db.get(Project, project_id)
    if not project or project.series_id != series.id:
        raise HTTPException(404, "Volume introuvable dans cette série.")
    if project.source_format != "epub":
        raise HTTPException(409, "Des chapitres TXT ou JSON appartiennent obligatoirement à une série.")
    project.series_id, project.volume_number = None, None
    db.flush()
    refresh_series(db, series.id)
    db.commit()
    return project_views(db, [project], bible=False)[0]


@router.get("/{series_id}/chapters")
def series_chapters(series_id: str, user: CurrentUser, db: DB, project_id: str | None = None):
    """Chapters of the series' text volumes (or of one volume), with the progress of each."""
    series_access(db, series_id, user)
    volumes = [
        p for p in db.scalars(readable_projects(db, user).where(Project.series_id == series_id))
        if project_id is None or p.id == project_id
    ]
    ids = [p.id for p in volumes]
    counts = {
        chapter_id: values
        for chapter_id, *values in db.execute(
            select(
                Segment.chapter_id,
                func.count(Segment.id),
                func.count(Segment.id).filter(Segment.translation != "", Segment.retained_source.is_(False)),
                func.count(Segment.id).filter(Segment.validated.is_(True)),
                func.count(Segment.id).filter(Segment.status.in_(("check", "error", "refused"))),
            )
            .where(Segment.project_id.in_(ids))
            .group_by(Segment.chapter_id)
        )
    }
    order = {p.id: index for index, p in enumerate(series_volumes(db, series_id))}
    chapters = sorted(
        db.scalars(select(Chapter).where(Chapter.project_id.in_(ids), Chapter.kind.in_(("narrative", "auxiliary")))),
        key=lambda c: (order.get(c.project_id, 0), c.position),
    )
    titles = {p.id: p.title for p in volumes}
    return [
        {
            "id": chapter.id,
            "project_id": chapter.project_id,
            "volume_title": titles[chapter.project_id],
            "position": chapter.position,
            "title": chapter.title,
            "chapter_number": chapter.chapter_number,
            "external_id": chapter.external_id,
            "analyzed": chapter.analyzed,
            "context_stale": chapter.context_stale,
            "segments": counts.get(chapter.id, (0, 0, 0, 0))[0],
            "translated": counts.get(chapter.id, (0, 0, 0, 0))[1],
            "validated": counts.get(chapter.id, (0, 0, 0, 0))[2],
            "flagged": counts.get(chapter.id, (0, 0, 0, 0))[3],
        }
        for chapter in chapters
    ]


class BibleInput(StrictModel):
    bible: dict
    validated: bool = True


@router.get("/{series_id}/bible")
def series_bible(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user)
    return {"bible": series.bible, "validated": series.bible_validated, "updated_at": series.updated_at}


@router.put("/{series_id}/bible")
def edit_series_bible(series_id: str, body: BibleInput, user: CurrentUser, db: DB):
    """A person's version replaces the derived one until they hand it back to the volumes."""
    series = series_access(db, series_id, user, owner=True)
    if len(str(body.bible)) > 2_000_000:
        raise HTTPException(413, "Series Bible trop volumineuse.")
    series.bible, series.bible_validated = body.bible, body.validated
    if not body.validated:
        refresh_series(db, series.id)
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.bible_edited", series_id=series.id,
          validated=body.validated)
    db.commit()
    return {"bible": series.bible, "validated": series.bible_validated, "updated_at": series.updated_at}


@router.post("/{series_id}/refresh")
def refresh(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    refresh_series(db, series.id)
    db.commit()
    return {"bible": series.bible, "validated": series.bible_validated, "updated_at": series.updated_at}


class SeriesTermInput(StrictModel):
    source: str = Field(min_length=1, max_length=300)
    translation: str = Field(min_length=1, max_length=300)
    category: str = Field(default="autre", max_length=50)
    description: str = Field(default="", max_length=4000)
    locked: bool = False
    accepted: bool = True


@router.get("/{series_id}/glossary")
def series_glossary(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user)
    titles = {p.id: (p.title, p.volume_number) for p in db.scalars(select(Project).where(Project.series_id == series.id))}
    overrides = [
        {
            "project_id": term.project_id,
            "volume_title": titles[term.project_id][0],
            "volume_number": titles[term.project_id][1],
            "source": term.source,
            "translation": term.translation,
            "locked": term.locked,
        }
        for term in db.scalars(
            select(Glossary).where(Glossary.project_id.in_(list(titles)), Glossary.series_override.is_(True))
        )
    ]
    return {
        "terms": [
            row(term)
            for term in db.scalars(select(SeriesTerm).where(SeriesTerm.series_id == series.id).order_by(SeriesTerm.source))
        ],
        "overrides": overrides,
    }


@router.post("/{series_id}/glossary", status_code=201)
def add_series_term(series_id: str, body: SeriesTermInput, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    existing = db.scalar(select(SeriesTerm).where(SeriesTerm.series_id == series.id, SeriesTerm.source == body.source))
    if existing:
        raise HTTPException(409, "Ce terme existe déjà dans le glossaire de la série.")
    term = SeriesTerm(series_id=series.id, origin="human", **body.model_dump())
    db.add(term)
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.term_added", series_id=series.id,
          source=term.source, translation=term.translation, locked=term.locked)
    db.commit()
    return row(term)


@router.put("/{series_id}/glossary/{term_id}")
def edit_series_term(series_id: str, term_id: str, body: SeriesTermInput, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    term = db.get(SeriesTerm, term_id)
    if not term or term.series_id != series.id:
        raise HTTPException(404, "Terme introuvable.")
    before = {"translation": term.translation, "locked": term.locked, "origin": term.origin}
    for key, value in body.model_dump().items():
        setattr(term, key, value)
    # A person's decision: the volumes no longer rewrite it.
    term.origin = "human"
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.term_edited", series_id=series.id,
          source=term.source, translation=term.translation, locked=term.locked, previous=before)
    db.commit()
    return row(term)


@router.delete("/{series_id}/glossary/{term_id}")
def delete_series_term(series_id: str, term_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    term = db.get(SeriesTerm, term_id)
    if not term or term.series_id != series.id:
        raise HTTPException(404, "Terme introuvable.")
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.term_deleted", series_id=series.id,
          source=term.source, translation=term.translation)
    db.delete(term)
    db.commit()
    return {"ok": True}


@router.get("/{series_id}/entities")
def series_entities(series_id: str, request: Request, user: CurrentUser, db: DB, category: str | None = None):
    series = series_access(db, series_id, user)
    query = select(SeriesEntity).where(SeriesEntity.series_id == series.id)
    if category:
        query = query.where(SeriesEntity.category == category)
    entities = list(db.scalars(query.order_by(SeriesEntity.category, SeriesEntity.first_volume_number, SeriesEntity.name)))
    links = list(
        db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.series_entity_id.in_([e.id for e in entities])))
    )
    locals_ = {e.id: e for e in db.scalars(select(Entity).where(Entity.id.in_([link.entity_id for link in links])))}
    titles = {p.id: (p.title, p.volume_number) for p in db.scalars(select(Project).where(Project.series_id == series.id))}
    by_entity: dict[str, list] = {}
    for link in links:
        local = locals_.get(link.entity_id)
        by_entity.setdefault(link.series_entity_id, []).append(
            {
                **row(link),
                "reason": localize(link.reason, preferred_language(request.headers.get("accept-language"))),
                "local_name": local.name if local else "",
                "local_aliases": (local.data.get("aliases", []) if local else []),
                "volume_title": titles.get(link.project_id, ("", None))[0],
                "volume_number": titles.get(link.project_id, ("", None))[1],
            }
        )
    return [{**row(entity), "links": by_entity.get(entity.id, [])} for entity in entities]


class SeriesEntityInput(StrictModel):
    name: str = Field(min_length=1, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    validated: bool = True


@router.put("/{series_id}/entities/{entity_id}")
def edit_series_entity(series_id: str, entity_id: str, body: SeriesEntityInput, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    entity = db.get(SeriesEntity, entity_id)
    if not entity or entity.series_id != series.id:
        raise HTTPException(404, "Identité introuvable.")
    clash = db.scalar(
        select(SeriesEntity).where(
            SeriesEntity.series_id == series.id, SeriesEntity.category == entity.category,
            SeriesEntity.name == body.name.strip(), SeriesEntity.id != entity.id,
        )
    )
    if clash:
        raise HTTPException(409, "Une autre identité de la série porte déjà ce nom : fusionnez-les plutôt.")
    before = {"name": entity.name, "aliases": entity.aliases}
    entity.name = body.name.strip()
    entity.aliases = [a.strip()[:300] for a in body.aliases if a.strip() and a.strip() != entity.name]
    entity.validated = body.validated
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.identity_edited", series_id=series.id,
          entity_id=entity.id, name=entity.name, aliases=entity.aliases, previous=before)
    db.commit()
    return row(entity)


class MergeInput(StrictModel):
    target_id: str
    source_ids: list[str] = Field(min_length=1, max_length=50)


@router.post("/{series_id}/entities/merge")
def merge_series_entities(series_id: str, body: MergeInput, user: CurrentUser, db: DB):
    """Two series identities are one person: only a person decides it."""
    series = series_access(db, series_id, user, owner=True)
    target = db.get(SeriesEntity, body.target_id)
    sources = [db.get(SeriesEntity, sid) for sid in body.source_ids]
    if not target or target.series_id != series.id or any(s is None or s.series_id != series.id for s in sources):
        raise HTTPException(404, "Identité introuvable.")
    if target.id in body.source_ids or any(s.category != target.category for s in sources):
        raise HTTPException(422, "Fusion impossible : choisissez des identités distinctes de même nature.")
    for source in sources:
        for link in db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.series_entity_id == source.id)):
            duplicate = db.scalar(
                select(SeriesEntityLink).where(
                    SeriesEntityLink.series_entity_id == target.id, SeriesEntityLink.entity_id == link.entity_id
                )
            )
            if duplicate:
                db.delete(link)
            else:
                link.series_entity_id = target.id
                link.status, link.human = "linked", True
        for relation in db.scalars(
            select(SeriesRelation).where(
                or_(SeriesRelation.source_id == source.id, SeriesRelation.target_id == source.id)
            )
        ):
            db.delete(relation)
        target.aliases = list(dict.fromkeys([*target.aliases, source.name, *source.aliases]))[:50]
        if source.first_volume_number is not None and (
            target.first_volume_number is None or source.first_volume_number < target.first_volume_number
        ):
            target.first_volume_number, target.first_project_id = source.first_volume_number, source.first_project_id
            target.first_position = source.first_position
        source.merged_into_id = target.id
    target.validated = True
    db.flush()
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.identities_merged", series_id=series.id,
          target_id=target.id, target=target.name, sources=[{"id": s.id, "name": s.name} for s in sources])
    refresh_series(db, series.id)
    db.commit()
    return row(canonical(db, target))


class SplitInput(StrictModel):
    link_ids: list[str] = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)


@router.post("/{series_id}/entities/{entity_id}/split")
def split_series_entity(series_id: str, entity_id: str, body: SplitInput, user: CurrentUser, db: DB):
    """Some volumes' characters were not this identity: they get their own, decided by a person."""
    series = series_access(db, series_id, user, owner=True)
    entity = db.get(SeriesEntity, entity_id)
    if not entity or entity.series_id != series.id:
        raise HTTPException(404, "Identité introuvable.")
    links = [db.get(SeriesEntityLink, lid) for lid in body.link_ids]
    if any(link is None or link.series_entity_id != entity.id for link in links):
        raise HTTPException(404, "Lien introuvable pour cette identité.")
    name = body.name.strip()
    if db.scalar(
        select(SeriesEntity.id).where(
            SeriesEntity.series_id == series.id, SeriesEntity.category == entity.category, SeriesEntity.name == name
        )
    ):
        raise HTTPException(409, "Une autre identité de la série porte déjà ce nom : fusionnez-les plutôt.")
    projects = {p.id: p for p in db.scalars(select(Project).where(Project.id.in_([link.project_id for link in links])))}
    first = min(links, key=lambda link: (projects[link.project_id].volume_number or 0))
    created = SeriesEntity(
        series_id=series.id,
        name=name,
        category=entity.category,
        aliases=[],
        data={},
        validated=True,
        first_project_id=first.project_id,
        first_volume_number=projects[first.project_id].volume_number,
    )
    db.add(created)
    db.flush()
    for link in links:
        link.series_entity_id, link.status, link.human = created.id, "linked", True
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.identity_split", series_id=series.id,
          entity_id=entity.id, name=entity.name, new_entity_id=created.id, new_name=name, links=body.link_ids)
    refresh_series(db, series.id)
    db.commit()
    return row(created)


class LinkInput(StrictModel):
    status: Literal["linked", "rejected"]


@router.put("/{series_id}/links/{link_id}")
def decide_link(series_id: str, link_id: str, body: LinkInput, user: CurrentUser, db: DB):
    """Confirms or rejects a proposed link between a volume's character and a series identity."""
    series = series_access(db, series_id, user, owner=True)
    link = db.get(SeriesEntityLink, link_id)
    entity = db.get(SeriesEntity, link.series_entity_id) if link else None
    if not link or not entity or entity.series_id != series.id:
        raise HTTPException(404, "Lien introuvable pour cette identité.")
    link.status, link.human, link.confidence = body.status, True, 1.0
    if body.status == "linked":
        # One identity per character of a volume: its other candidate links are set aside.
        for other in db.scalars(
            select(SeriesEntityLink).where(SeriesEntityLink.entity_id == link.entity_id, SeriesEntityLink.id != link.id)
        ):
            other.status, other.human = "rejected", True
    audit(db, owner_id=series.owner_id, actor_id=user.id, action=f"series.link_{body.status}", series_id=series.id,
          link_id=link.id, entity_id=entity.id, name=entity.name, project_id=link.project_id)
    refresh_series(db, series.id)
    db.commit()
    return row(link)


@router.get("/{series_id}/relations")
def series_relations(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user)
    names = {e.id: e.name for e in db.scalars(select(SeriesEntity).where(SeriesEntity.series_id == series.id))}
    return [
        {**row(relation), "source": names.get(relation.source_id, ""), "target": names.get(relation.target_id, "")}
        for relation in db.scalars(
            select(SeriesRelation)
            .where(SeriesRelation.series_id == series.id)
            .order_by(SeriesRelation.first_volume_number, SeriesRelation.first_position)
        )
    ]


@router.get("/{series_id}/audit")
def series_audit(series_id: str, user: CurrentUser, db: DB):
    series = series_access(db, series_id, user, owner=True)
    return [
        row(entry)
        for entry in db.scalars(
            select(AuditEntry)
            .where(AuditEntry.series_id == series.id)
            .order_by(AuditEntry.created_at.desc())
            .limit(200)
        )
    ]


@router.get("/{series_id}/memory")
def series_memory(series_id: str, user: CurrentUser, db: DB):
    """External memory of the series: its place and what remains to be written, volume by volume."""
    series = series_access(db, series_id, user)
    volumes = [
        p for p in series_volumes(db, series.id) if p.owner_id == user.id or db.get(Membership, (p.id, user.id))
    ]
    counts: dict[str, dict[str, int]] = {}
    for pid, status, count in db.execute(
        select(Outbox.project_id, Outbox.status, func.count())
        .where(Outbox.project_id.in_([p.id for p in volumes]))
        .group_by(Outbox.project_id, Outbox.status)
    ):
        counts.setdefault(pid, {})[status] = count
    config = memory_config()
    rows = [
        {
            "project_id": p.id,
            "title": p.title,
            "volume_number": p.volume_number,
            "context_backend": p.context_backend,
            "sent": counts.get(p.id, {}).get("sent", 0),
            "pending": counts.get(p.id, {}).get("pending", 0),
            "failed": counts.get(p.id, {}).get("error", 0),
        }
        for p in volumes
    ]
    return {
        "backends": sorted({p.context_backend for p in volumes}),
        "configured": bool(config["base_url"]),
        "root_uri": series_uri(series.owner_id, series.id) if config["base_url"] or config["root_uri"] else "",
        "sent": sum(r["sent"] for r in rows),
        "pending": sum(r["pending"] for r in rows),
        "failed": sum(r["failed"] for r in rows),
        "volumes": rows,
    }


@router.post("/{series_id}/memory/{action}")
async def series_memory_action(
    series_id: str, action: Literal["resync", "rebuild", "reindex"], user: CurrentUser, db: DB
):
    """resync retries what is waiting now; rebuild rewrites every event of the series from SQL;
    reindex asks OpenViking to recompute its index of the series space. Nothing is ever deleted remotely."""
    series = series_access(db, series_id, user, owner=True)
    volumes = [p for p in series_volumes(db, series.id) if p.context_backend != "internal"]
    if action == "reindex":
        if not memory_config()["base_url"]:
            raise HTTPException(409, "OpenViking n’est pas configuré sur ce serveur.")
        try:
            async with OpenVikingClient(memory_config()) as client:
                result = await client.reindex(series_uri(series.owner_id, series.id))
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                502,
                f"OpenViking refuse la réindexation (HTTP {exc.response.status_code}). "
                "Vérifiez les droits de la clé sur cette racine. La reconstruction par réécriture "
                "depuis SQL reste disponible.",
            ) from None
        return {"accepted": True, "queued": 0, "result": result}
    queued = 0
    for project in volumes:
        if action == "rebuild":
            queued += ensure_events(db, project, force=True)
            queue_catalog(db, project, force=True)
        for event in db.scalars(select(Outbox).where(Outbox.project_id == project.id, Outbox.status != "sent")):
            event.next_attempt, event.error = 0, ""
            queued += action == "resync"
    db.commit()
    return {"accepted": True, "queued": queued, "volumes": len(volumes)}
