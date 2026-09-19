"""Memory events as OpenViking receives them, derived from SQL alone.

The document written for a memory is a pure function of SQL rows: rebuilding the external space,
checking what OpenViking returns, and moving a volume into a series all compare against this. The
outbox only carries the work of writing it.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.context.series import prior_volumes
from app.models import AppSetting, Chapter, Memory, Outbox, Project, Segment
from app.providers.openviking import LAYOUT_VERSION, MEMORY_KINDS, memory_event_uri, project_uri

SCHEMA_VERSION = 2


def identities_of(memory: Memory) -> list[str]:
    content = memory.content or {}
    names = [c.get("canonical_name", "") for c in content.get("characters", []) if isinstance(c, dict)]
    for event in content.get("events", []) if isinstance(content.get("events"), list) else []:
        if isinstance(event, dict):
            names += [name for name in event.get("known_by", []) if isinstance(name, str)]
    return list(dict.fromkeys(name for name in names if name))[:50]


def canonical_event(memory: Memory, project: Project, chapter: Chapter | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "owner_id": project.owner_id,
        "series_id": project.series_id,
        "project_id": project.id,
        "volume_number": project.volume_number,
        "chapter_id": chapter.id if chapter else None,
        "chapter_position": chapter.position if chapter else None,
        "chapter_number": chapter.chapter_number if chapter else None,
        "segment_id": memory.segment_id,
        # Narrative position: the passage's position in its volume.
        "position": memory.position,
        "type": memory.kind,
        "identities": identities_of(memory),
        "validated": memory.validated,
        "created_at": memory.created_at,
        "content": memory.content,
    }


def chapters_by_segment(db: Session, segment_ids: list[str]) -> dict[str, Chapter]:
    if not segment_ids:
        return {}
    rows = db.execute(
        select(Segment.id, Chapter).join(Chapter, Chapter.id == Segment.chapter_id).where(Segment.id.in_(segment_ids))
    ).all()
    return {segment_id: chapter for segment_id, chapter in rows}


def canonical_events(db: Session, memories: list[Memory], projects: dict[str, Project]) -> dict[str, dict]:
    chapters = chapters_by_segment(db, [m.segment_id for m in memories if m.segment_id])
    return {
        memory.id: canonical_event(memory, projects[memory.project_id], chapters.get(memory.segment_id))
        for memory in memories
    }


def still_valid(db: Session, memory: Memory, latest_human: dict[str, str]) -> bool:
    """Superseded human analyses and decisions on a passage edited since never count."""
    if memory.validated and memory.kind == "analysis":
        return latest_human.get(memory.segment_id) == memory.id
    if memory.validated:
        segment = db.get(Segment, memory.segment_id) if memory.segment_id else None
        return bool(segment and segment.validated and memory.content.get("revision") == segment.revision)
    return True


def latest_human_analyses(memories: list[Memory]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for memory in sorted(memories, key=lambda m: m.created_at):
        if memory.kind == "analysis" and memory.validated:
            latest[memory.segment_id] = memory.id
    return latest


def admitted(db: Session, project: Project, position: int) -> dict[str, tuple[Memory, Project]]:
    """Events a passage at `position` may read, by URI: earlier passages of its volume (a person's
    analysis of the passage itself included) and earlier volumes of its series. Nothing later."""
    admitted_: dict[str, tuple[Memory, Project]] = {}
    own = list(db.scalars(select(Memory).where(Memory.project_id == project.id, Memory.position <= position)))
    latest = latest_human_analyses(own)
    for memory in own:
        timely = memory.position < position or (
            memory.position == position and memory.validated and memory.kind == "analysis"
        )
        if memory.kind in MEMORY_KINDS and timely and still_valid(db, memory, latest):
            admitted_[memory_event_uri(project, memory.id)] = (memory, project)
    for volume in prior_volumes(db, project):
        earlier = list(db.scalars(select(Memory).where(Memory.project_id == volume.id)))
        latest = latest_human_analyses(earlier)
        for memory in earlier:
            if memory.kind in MEMORY_KINDS and still_valid(db, memory, latest):
                admitted_[memory_event_uri(volume, memory.id)] = (memory, volume)
    return admitted_


def ensure_events(db: Session, project: Project, *, force: bool = False) -> int:
    """Every memory of the volume has an outbox row with its current document and place.

    Rows deleted by the retention are recreated, rows written at another place (0.5 layout, volume
    moved to or from a series) or with outdated metadata are written again. Remote copies left at an
    old place are never deleted: they are simply no longer admitted.
    """
    memories = list(db.scalars(select(Memory).where(Memory.project_id == project.id, Memory.kind.in_(MEMORY_KINDS))))
    documents = canonical_events(db, memories, {project.id: project})
    rows = {
        row.event_key: row
        for row in db.scalars(
            select(Outbox).where(Outbox.project_id == project.id, Outbox.event_key.in_([m.id for m in memories]))
        )
    }
    queued = 0
    for memory in memories:
        document = documents[memory.id]
        row = rows.get(memory.id)
        if row is None:
            db.add(
                Outbox(
                    project_id=project.id,
                    event_key=memory.id,
                    session_name=f"chapter-{document['chapter_id']}",
                    payload=document,
                )
            )
            queued += 1
            continue
        current = row.uri == memory_event_uri(project, memory.id) and row.payload == document
        if force or not current:
            row.payload, row.status, row.next_attempt, row.error = document, "pending", 0, ""
            queued += 1
    db.flush()
    return queued


def layout_outdated(db: Session, project: Project) -> bool:
    """Cheap check on the latest written event: is the volume's space still where it was written?"""
    row = db.scalar(
        select(Outbox)
        .where(Outbox.project_id == project.id, Outbox.status == "sent", Outbox.session_name != "catalog")
        .order_by(Outbox.created_at.desc())
        .limit(1)
    )
    if row is None:
        return False
    if not (row.uri or "").startswith(project_uri(project) + "/"):
        return True
    payload = row.payload or {}
    return payload.get("type") in MEMORY_KINDS and (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("series_id") != project.series_id
        or payload.get("volume_number") != project.volume_number
    )


def refresh_layouts() -> int:
    """Replays the events of volumes whose external space moved; once after the 0.6 upgrade, every
    volume using OpenViking is rewritten from SQL into the series or standalone layout."""
    from app.db import SessionLocal

    queued = 0
    with SessionLocal() as db:
        marker = db.get(AppSetting, "openviking_layout")
        migrating = not marker or marker.value.get("version", 1) < LAYOUT_VERSION
        for project in db.scalars(select(Project).where(Project.context_backend != "internal")):
            if migrating or layout_outdated(db, project):
                queued += ensure_events(db, project)
        if migrating:
            if marker:
                marker.value = {"version": LAYOUT_VERSION}
            else:
                db.add(AppSetting(key="openviking_layout", value={"version": LAYOUT_VERSION}))
        db.commit()
    return queued
