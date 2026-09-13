import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.memory.identities import upsert_profiles
from app.models import Glossary, Memory, Outbox, Project, Segment


def propose_terms(db: Session, project: Project, terms: list[dict]) -> None:
    for term in terms:
        source = term.get("source", "").strip()[:300]
        translation = term.get("translation", "").strip()[:300]
        if not source or not translation:
            continue
        existing = db.scalar(
            select(Glossary).where(Glossary.project_id == project.id, Glossary.source == source)
        )
        if not existing:
            db.add(
                Glossary(
                    project_id=project.id,
                    source=source,
                    translation=translation,
                    category=term.get("category", "autre")[:50],
                    description=term.get("description", ""),
                    accepted=project.config.get("auto_glossary", False),
                )
            )
            db.flush()


def characters(db: Session, project_id: str, profiles: list[dict], position: int) -> None:
    upsert_profiles(db, project_id, profiles, position)


def remember(
    db: Session, project: Project, segment: Segment, content: dict, kind: str, validated: bool = False
) -> None:
    memory = Memory(
        project_id=project.id,
        segment_id=segment.id,
        position=segment.position,
        content=content,
        kind=kind,
        validated=validated,
    )
    db.add(memory)
    db.flush()
    db.add(
        Outbox(
            project_id=project.id,
            event_key=memory.id,
            session_name=f"chapter-{segment.chapter_id}",
            payload={
                "type": kind,
                "position": segment.position,
                "segment_id": segment.id,
                "validated": validated,
                "content": content,
                "timestamp": time.time(),
            },
        )
    )


def invalidate_after_decision(db: Session, project: Project, source: str = "") -> int:
    project.memory_revision += 1
    affected = db.scalars(
        select(Segment).where(
            Segment.project_id == project.id, Segment.translation != "", Segment.validated.is_(False)
        )
    ).all()
    count = 0
    for segment in affected:
        if not source or source.casefold() in segment.source.casefold():
            segment.status = "check"
            count += 1
    return count
