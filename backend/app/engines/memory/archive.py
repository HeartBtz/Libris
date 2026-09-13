from sqlalchemy.orm import Session

from app.models import CharacterRelation, Entity, EntityMerge
from app.schemas import Character


def restore_graph(db: Session, pid: str, payload: dict, segment_map: dict[str, str]) -> None:
    rows: dict[str, Entity] = {}
    for item in payload.get("entities", []):
        data = item["data"]
        if item["category"] == "character":
            data = Character.model_validate(
                {k: v for k, v in data.items() if k in Character.model_fields}
            ).model_dump() | {"first_position": int(data.get("first_position", -1))}
        entity = Entity(
            project_id=pid,
            name=str(item["name"])[:300],
            category=str(item["category"])[:50],
            data=data,
            validated=bool(item.get("validated")),
            identity_validated=bool(item.get("identity_validated")),
        )
        db.add(entity)
        db.flush()
        rows[item["id"]] = entity
    redirects = {item["id"]: item.get("merged_into_id") for item in payload.get("entities", [])}
    for key, parent in redirects.items():
        seen = {key}
        while parent:
            if parent in seen or parent not in rows:
                raise ValueError("Graphe d’identités importé incohérent ou cyclique.")
            seen.add(parent)
            next_parent = redirects.get(parent)
            if not next_parent:
                rows[key].merged_into_id = rows[parent].id
            parent = next_parent
    for item in payload.get("character_relations", []):
        if item["source_id"] not in rows or item["target_id"] not in rows:
            raise ValueError("Relation sans personnage dans l’archive.")
        db.add(
            CharacterRelation(
                project_id=pid,
                source_id=rows[item["source_id"]].id,
                target_id=rows[item["target_id"]].id,
                segment_id=segment_map.get(item.get("segment_id")),
                position=int(item.get("position", -1)),
                relation_type=str(item["relation_type"])[:80],
                description=str(item.get("description", "")),
                evidence=str(item.get("evidence", "")),
                provenance=str(item.get("provenance", "import"))[:30],
                validated=bool(item.get("validated")),
                active=bool(item.get("active", True)),
            )
        )
    for item in payload.get("entity_merges", []):
        if item["target_id"] in rows and all(s in rows for s in item["source_ids"]):
            db.add(
                EntityMerge(
                    project_id=pid,
                    target_id=rows[item["target_id"]].id,
                    source_ids=[rows[s].id for s in item["source_ids"]],
                    snapshots=item["snapshots"],
                    reason=str(item.get("reason", "Import")),
                    human=bool(item.get("human")),
                )
            )
