from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.epub.text import plain
from app.engines.memory.identities import effective_names, identities, mention, plausible_name, resolve
from app.models import CharacterRelation, Segment


def collect(db: Session, pid: str, relations: list[dict], segment: Segment) -> None:
    for relation in relations:
        source = resolve(db, pid, relation["source"])
        target = resolve(db, pid, relation["target"])
        if not source or not target or source.id == target.id:
            continue
        evidence = relation.get("evidence", "")
        if evidence and " ".join(evidence.split()) not in " ".join(plain(segment.source).split()):
            evidence = ""  # An invented quote must never be presented as source evidence.
        existing = db.scalar(
            select(CharacterRelation).where(
                CharacterRelation.project_id == pid,
                CharacterRelation.source_id == source.id,
                CharacterRelation.target_id == target.id,
                CharacterRelation.relation_type == relation["relation_type"],
                CharacterRelation.segment_id == segment.id,
            )
        )
        if not existing:
            db.add(
                CharacterRelation(
                    project_id=pid,
                    source_id=source.id,
                    target_id=target.id,
                    segment_id=segment.id,
                    position=segment.position,
                    relation_type=relation["relation_type"][:80],
                    description=relation.get("description", ""),
                    evidence=evidence,
                    provenance="analysis",
                )
            )


def backfill(db: Session, pid: str) -> int:
    """Links from legacy profile text are explicitly suggestions, not newly asserted facts."""
    rows = identities(db, pid)
    added = 0
    for source in rows:
        if not plausible_name(source.name):
            continue
        for description in source.data.get("relationships", []):
            for target in rows:
                if target.id == source.id or not any(
                    mention(n, description) for n in effective_names(target, rows)
                ):
                    continue
                exists = db.scalar(
                    select(CharacterRelation.id).where(
                        CharacterRelation.project_id == pid,
                        CharacterRelation.source_id == source.id,
                        CharacterRelation.target_id == target.id,
                        CharacterRelation.description == description,
                    )
                )
                if not exists:
                    db.add(
                        CharacterRelation(
                            project_id=pid,
                            source_id=source.id,
                            target_id=target.id,
                            relation_type="relation évoquée",
                            description=description,
                            provenance="profile_suggestion",
                            position=int(source.data.get("first_position", -1)),
                        )
                    )
                    db.flush()
                    added += 1
    return added
