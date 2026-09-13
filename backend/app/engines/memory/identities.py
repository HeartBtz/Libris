import copy
import re
import unicodedata
from difflib import SequenceMatcher

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CharacterRelation, Entity, EntityMerge, Project
from app.schemas import Character


def normalized(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def plausible_name(value: str) -> bool:
    name = normalized(value)
    generic = {
        "i",
        "me",
        "myself",
        "he",
        "she",
        "it",
        "we",
        "you",
        "they",
        "him",
        "her",
        "il",
        "elle",
        "je",
        "moi",
        "nous",
        "vous",
        "narrator",
        "narrateur",
        "my mother",
        "my father",
        "ma mère",
        "mon père",
        "mother",
        "father",
    }
    return (
        len(name) >= 1
        and name not in generic
        and not re.search(
            r"[’']s\s+(friend|mother|father|son|daughter|child|brother|sister|teacher|student)\b|\b(friend|ami|amie)\s+(of|de)\b",
            name,
        )
    )


def identities(db: Session, pid: str) -> list[Entity]:
    return list(
        db.scalars(
            select(Entity)
            .where(Entity.project_id == pid, Entity.category == "character", Entity.merged_into_id.is_(None))
            .order_by(Entity.created_at)
        )
    )


def names(entity: Entity) -> set[str]:
    return {
        normalized(n)
        for n in [entity.name, *entity.data.get("aliases", [])]
        if entity.validated or entity.identity_validated or plausible_name(n)
    }


def resolve(db: Session, pid: str, name: str) -> Entity | None:
    found = [e for e in identities(db, pid) if normalized(name) in names(e)]
    confirmed = [e for e in found if e.validated or e.identity_validated]
    if confirmed:
        found = confirmed
    return found[0] if len(found) == 1 else None


def effective_names(entity: Entity, registry: list[Entity]) -> list[str]:
    protected = entity.validated or entity.identity_validated
    return [
        n
        for n in [entity.name, *entity.data.get("aliases", [])]
        if (protected or plausible_name(n))
        and (
            protected
            or not any(
                other.id != entity.id
                and (other.validated or other.identity_validated)
                and normalized(n) in names(other)
                for other in registry
            )
        )
    ]


def merged_profile(target: dict, source: dict) -> dict:
    result = dict(target)
    for key in Character.model_fields:
        value = source.get(key)
        if value is None:
            continue
        if not result.get(key):
            result[key] = value
        elif isinstance(value, list):
            result[key] = list(dict.fromkeys([*result[key], *value]))
    return result


def merge(db: Session, pid: str, target_id: str, source_ids: list[str], reason: str, human: bool) -> Entity:
    rows = list(
        db.scalars(
            select(Entity)
            .where(Entity.project_id == pid, Entity.id.in_([target_id, *source_ids]))
            .order_by(Entity.id)
            .with_for_update()
        )
    )
    mapping = {e.id: e for e in rows}
    if not source_ids or target_id in source_ids or set(mapping) != {target_id, *source_ids}:
        raise ValueError("La fusion doit cibler des fiches distinctes du même projet.")
    if any(e.merged_into_id or e.category != "character" for e in rows):
        raise ValueError("Choisissez des identités canoniques de personnages.")
    if not human and any(e.validated or e.identity_validated for e in rows):
        raise ValueError("Cette fusion nécessite la validation humaine des identités protégées.")
    target = mapping[target_id]
    snapshots = [
        {
            "id": e.id,
            "name": e.name,
            "data": copy.deepcopy(e.data),
            "validated": e.validated,
            "identity_validated": e.identity_validated,
        }
        for e in rows
    ]
    data = dict(target.data)
    aliases = [*data.get("aliases", [])] if not human or target.identity_validated or target.validated else []
    for sid in dict.fromkeys(source_ids):
        source = mapping[sid]
        aliases.append(source.name)
        if not human or source.identity_validated or source.validated:
            aliases += source.data.get("aliases", [])
        data = merged_profile(data, source.data)
        data["first_position"] = min(data.get("first_position", 0), source.data.get("first_position", 0))
        source.merged_into_id = target.id
    data["canonical_name"] = target.name
    outside = [e for e in identities(db, pid) if e.id not in {target_id, *source_ids}]
    data["aliases"] = list(
        dict.fromkeys(
            n
            for n in aliases
            if plausible_name(n)
            and normalized(n) != normalized(target.name)
            and not any(normalized(n) in names(e) for e in outside)
        )
    )
    if human:
        inherited = [n for e in rows for n in e.data.get("aliases", [])]
        data["proposed_aliases"] = list(
            dict.fromkeys(
                n
                for n in [*data.get("proposed_aliases", []), *inherited]
                if plausible_name(n)
                and normalized(n) != normalized(target.name)
                and normalized(n) not in {normalized(a) for a in data["aliases"]}
            )
        )
    target.data = data
    target.identity_validated = target.identity_validated or human
    for child in db.scalars(
        select(Entity).where(Entity.project_id == pid, Entity.merged_into_id.in_(source_ids))
    ):
        child.merged_into_id = target.id
    for edge in db.scalars(
        select(CharacterRelation).where(
            CharacterRelation.project_id == pid,
            or_(CharacterRelation.source_id.in_(source_ids), CharacterRelation.target_id.in_(source_ids)),
        )
    ):
        if edge.source_id in source_ids:
            edge.source_id = target.id
        if edge.target_id in source_ids:
            edge.target_id = target.id
        if edge.source_id == edge.target_id:
            edge.active = False
    db.add(
        EntityMerge(
            project_id=pid,
            target_id=target.id,
            source_ids=source_ids,
            snapshots=snapshots,
            reason=reason,
            human=human,
        )
    )
    db.get(Project, pid).memory_revision += 1
    db.flush()
    return target


def upsert_profiles(db: Session, pid: str, profiles: list[dict], position: int, human: bool = False) -> None:
    for profile in profiles:
        name = profile.get("canonical_name", "").strip()[:300]
        if not name or (not human and not plausible_name(name)):
            continue
        profile = dict(profile, aliases=[a for a in profile.get("aliases", []) if plausible_name(a)])
        declared = {normalized(n) for n in [name, *profile.get("aliases", [])] if n}
        known = identities(db, pid)
        matches = [e for e in known if names(e) & declared]
        confirmed = [
            e for e in matches if (e.validated or e.identity_validated) and normalized(name) in names(e)
        ]
        if len(confirmed) == 1:
            matches = confirmed
        exact = next((e for e in matches if normalized(e.name) == normalized(name)), None)
        if len(matches) > 1:
            target = (exact if human else None) or max(matches, key=lambda e: len(e.name))
            if human or not any(e.validated or e.identity_validated for e in matches):
                target = merge(
                    db,
                    pid,
                    target.id,
                    [e.id for e in matches if e.id != target.id],
                    f"Alias explicitement déclaré dans l’analyse au passage {position + 1}.",
                    human,
                )
            else:
                target = exact
        else:
            target = matches[0] if matches else None
        if (
            target
            and not human
            and (target.identity_validated or target.validated)
            and normalized(name) not in names(target)
        ):
            # A new name suggested by the model cannot silently extend a confirmed identity.
            profile = dict(
                profile,
                proposed_aliases=list(
                    dict.fromkeys([*profile.get("proposed_aliases", []), *profile.get("aliases", [])])
                ),
            )
            target = None
            profile = dict(
                profile,
                aliases=[
                    a for a in profile.get("aliases", []) if not any(normalized(a) in names(e) for e in known)
                ],
            )
        if not target:
            # Never resurrect a merged name as a separate character.
            retired = db.scalar(
                select(Entity).where(
                    Entity.project_id == pid, Entity.name == name, Entity.category == "character"
                )
            )
            target = db.get(Entity, retired.merged_into_id) if retired and retired.merged_into_id else retired
        if not target:
            if not human:
                blocked = [
                    a
                    for a in profile.get("aliases", [])
                    if any((e.validated or e.identity_validated) and normalized(a) in names(e) for e in known)
                ]
                profile = dict(
                    profile,
                    aliases=[a for a in profile.get("aliases", []) if a not in blocked],
                    proposed_aliases=list(dict.fromkeys([*profile.get("proposed_aliases", []), *blocked])),
                )
            target = Entity(
                project_id=pid,
                name=name,
                category="character",
                data=dict(profile, first_position=position),
                validated=human,
                identity_validated=human,
            )
            db.add(target)
        else:
            data = (
                dict(target.data) if target.validated and not human else merged_profile(target.data, profile)
            )
            aliases = [*data.get("aliases", [])]
            if normalized(name) != normalized(target.name):
                aliases.append(name)
                if not target.identity_validated and not target.validated and len(name) > len(target.name):
                    aliases.append(target.name)
                    target.name = name
            # Ambiguous names bound to another protected identity are not adopted silently.
            safe_aliases = [
                n
                for n in [*aliases, *profile.get("aliases", [])]
                if (n in target.data.get("aliases", []) and (target.validated or target.identity_validated))
                or not any(
                    e.id != target.id and not e.merged_into_id and normalized(n) in names(e)
                    for e in identities(db, pid)
                )
            ]
            data["aliases"] = list(
                dict.fromkeys(n for n in safe_aliases if normalized(n) != normalized(target.name))
            )
            if not human and (target.identity_validated or target.validated):
                data["proposed_aliases"] = list(
                    dict.fromkeys(
                        n
                        for n in [*data.get("proposed_aliases", []), *profile.get("aliases", [])]
                        if plausible_name(n) and normalized(n) not in names(target)
                    )
                )
                data["aliases"] = list(target.data.get("aliases", []))
            data["canonical_name"] = target.name
            target.data = data
            if human:
                target.data = {**data, **profile, "canonical_name": target.name, "aliases": data["aliases"]}
                target.validated = target.identity_validated = True
        db.flush()


def suggestions(db: Session, pid: str) -> list[dict]:
    result = []
    rows = identities(db, pid)
    for i, left in enumerate(rows):
        for right in rows[i + 1 :]:
            if not plausible_name(left.name) or not plausible_name(right.name):
                continue
            a, b = normalized(left.name).split()[0], normalized(right.name).split()[0]
            score = SequenceMatcher(None, a, b).ratio()
            explicit = bool(names(left) & names(right))
            proposed = bool(
                {normalized(n) for n in left.data.get("proposed_aliases", [])} & names(right)
                or {normalized(n) for n in right.data.get("proposed_aliases", [])} & names(left)
            )
            if explicit or proposed or score >= 0.58:
                target, source = sorted([left, right], key=lambda e: len(e.name), reverse=True)
                result.append(
                    {
                        "target_id": target.id,
                        "source_id": source.id,
                        "score": 1 if explicit else score,
                        "reason": "Alias partagé"
                        if explicit
                        else "Rapprochement proposé par l’analyse"
                        if proposed
                        else "Noms proches : identité à confirmer",
                    }
                )
    return sorted(result, key=lambda p: -p["score"])[:100]


def canonical_bible(db: Session, project: Project) -> dict:
    if not project.bible:
        return {}
    return {
        **project.bible,
        "characters": [
            {k: v for k, v in e.data.items() if k in Character.model_fields}
            for e in identities(db, project.id)
        ],
    }


def mention(name: str, value: str) -> bool:
    return bool(plausible_name(name) and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", value, re.I))
