from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.common import row
from app.engines.memory.identities import identities, merge, names, normalized, suggestions
from app.engines.memory.relations import backfill
from app.engines.memory.store import invalidate_after_decision
from app.jobs.queue import emit
from app.models import CharacterRelation, Entity, EntityMerge
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api/projects/{pid}/characters")


@router.get("/graph")
def graph(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user)
    entities = identities(db, pid)
    edges = list(
        db.scalars(
            select(CharacterRelation).where(
                CharacterRelation.project_id == pid, CharacterRelation.active.is_(True)
            )
        )
    )
    return {
        "nodes": [row(e) for e in entities],
        "edges": [row(e) for e in edges],
        "suggestions": suggestions(db, pid),
        "merges": [
            row(m)
            for m in db.scalars(
                select(EntityMerge)
                .where(EntityMerge.project_id == pid)
                .order_by(EntityMerge.created_at.desc())
                .limit(30)
            )
        ],
    }


class MergeInput(BaseModel):
    target_id: str
    source_ids: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(default="Identité commune confirmée par l’utilisateur.", max_length=2000)


@router.post("/merge")
def merge_characters(pid: str, body: MergeInput, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    result = merge(db, pid, body.target_id, body.source_ids, body.reason, human=True)
    invalidate_after_decision(db, project)
    emit(db, pid, operation="identity_merge", target_id=result.id, status="memory_updated")
    db.commit()
    return row(result)


class AliasesInput(BaseModel):
    aliases: list[str] = Field(max_length=100)


@router.put("/{eid}/aliases")
def aliases(pid: str, eid: str, body: AliasesInput, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    entity = db.get(Entity, eid)
    if not entity or entity.project_id != pid or entity.merged_into_id:
        raise HTTPException(404, "Identité canonique introuvable.")
    values = [a.strip() for a in body.aliases if a.strip()]
    if any(len(a) > 300 for a in values):
        raise ValueError("Alias trop long.")
    for other in identities(db, pid):
        if other.id != eid and any(normalized(a) in names(other) for a in values):
            raise HTTPException(
                409, "Cet alias désigne déjà une autre fiche. Utilisez la fusion d’identités."
            )
    # An additive action does not silently undo earlier merges.
    entity.data = {**entity.data, "aliases": list(dict.fromkeys([*entity.data.get("aliases", []), *values]))}
    accepted = {normalized(a) for a in entity.data["aliases"]}
    entity.data = {
        **entity.data,
        "proposed_aliases": [
            a for a in entity.data.get("proposed_aliases", []) if normalized(a) not in accepted
        ],
    }
    entity.identity_validated = True
    invalidate_after_decision(db, project)
    db.commit()
    return row(entity)


@router.post("/rebuild-links")
def rebuild_links(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user, write=True)
    count = backfill(db, pid)
    db.commit()
    return {"added": count, "message": "Liens proposés à partir des fiches existantes ; à vérifier."}


class RelationInput(BaseModel):
    source_id: str
    target_id: str
    relation_type: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=4000)


def endpoints(db, pid, body):
    entities = {e.id for e in identities(db, pid)}
    if body.source_id not in entities or body.target_id not in entities or body.source_id == body.target_id:
        raise ValueError("Une relation doit relier deux identités distinctes de ce projet.")


@router.post("/relations")
def add_relation(pid: str, body: RelationInput, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    endpoints(db, pid, body)
    relation = CharacterRelation(project_id=pid, **body.model_dump(), validated=True, provenance="human")
    db.add(relation)
    project.memory_revision += 1
    db.commit()
    return row(relation)


@router.put("/relations/{rid}")
def edit_relation(pid: str, rid: str, body: RelationInput, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    endpoints(db, pid, body)
    relation = db.get(CharacterRelation, rid)
    if not relation or relation.project_id != pid:
        raise HTTPException(404, "Relation introuvable.")
    for key, value in body.model_dump().items():
        setattr(relation, key, value)
    relation.validated = True
    project.memory_revision += 1
    db.commit()
    return row(relation)


@router.delete("/relations/{rid}")
def dismiss_relation(pid: str, rid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    relation = db.get(CharacterRelation, rid)
    if not relation or relation.project_id != pid:
        raise HTTPException(404, "Relation introuvable.")
    relation.active = False
    project.memory_revision += 1
    db.commit()
    return {"ok": True}
