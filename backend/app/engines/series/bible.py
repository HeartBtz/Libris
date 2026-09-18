"""Series memory: canonical identities, relations, glossary and the Series Bible, derived from the volumes.

Everything here is recomputed from SQL and idempotent. Human decisions are never undone by a refresh:
confirmed or rejected links, merged identities, human series terms and a validated Series Bible stay.
Two different identities are never merged automatically: an ambiguous match stays a proposal.
"""

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.memory.identities import names, normalized, plausible_name
from app.models import (
    CharacterRelation,
    Entity,
    Glossary,
    Project,
    Series,
    SeriesEntity,
    SeriesEntityLink,
    SeriesRelation,
    SeriesTerm,
)

WORLD_LISTS = {"locations": "location", "organizations": "organization", "important_objects": "object"}
CONVENTION_LISTS = ("formatting_conventions", "honorifics", "translation_guidelines", "known_wordplay")
STYLE_KEYS = ("genre", "tone", "narrative_style", "narrative_point_of_view", "tense", "target_audience")


def reading_order(volumes: list[Project]) -> list[Project]:
    """Numbered volumes first, in number order; then the continuous container and unnumbered volumes."""
    return sorted(
        volumes,
        key=lambda p: (p.volume_number is None, p.volume_number or 0, p.project_kind == "serial", p.created_at),
    )


def series_volumes(db: Session, series_id: str) -> list[Project]:
    return reading_order(list(db.scalars(select(Project).where(Project.series_id == series_id))))


def _series_names(entity: SeriesEntity) -> set[str]:
    return {normalized(value) for value in [entity.name, *entity.aliases] if value}


def canonical(db: Session, entity: SeriesEntity) -> SeriesEntity:
    seen = set()
    while entity.merged_into_id and entity.id not in seen:
        seen.add(entity.id)
        target = db.get(SeriesEntity, entity.merged_into_id)
        if not target:
            break
        entity = target
    return entity


def link_characters(db: Session, series: Series, volume: Project) -> None:
    registry = [
        entity
        for entity in db.scalars(
            select(SeriesEntity).where(
                SeriesEntity.series_id == series.id,
                SeriesEntity.category == "character",
                SeriesEntity.merged_into_id.is_(None),
            )
        )
    ]
    locals_ = db.scalars(
        select(Entity).where(
            Entity.project_id == volume.id, Entity.category == "character", Entity.merged_into_id.is_(None)
        )
    ).all()
    decided = {
        link.entity_id
        for link in db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.project_id == volume.id))
        if link.status == "linked" or link.human
    }
    for entity in locals_:
        if entity.id in decided:
            continue
        own = names(entity)
        exact = [candidate for candidate in registry if normalized(entity.name) == normalized(candidate.name)]
        shared = [candidate for candidate in registry if own & _series_names(candidate)]
        matches = exact if len(exact) == 1 else shared
        previous = {
            link.series_entity_id: link
            for link in db.scalars(select(SeriesEntityLink).where(SeriesEntityLink.entity_id == entity.id))
        }
        if len(matches) == 1:
            target = matches[0]
            link = previous.get(target.id) or SeriesEntityLink(
                series_entity_id=target.id, entity_id=entity.id, project_id=volume.id
            )
            link.status = "linked"
            link.confidence = 1.0 if exact else 0.8
            link.reason = "Même nom canonique" if exact else "Nom ou alias partagé"
            db.add(link)
            for stale in previous.values():
                if stale.series_entity_id != target.id and not stale.human:
                    db.delete(stale)
            target.aliases = _merged_aliases(target, [entity.name, *entity.data.get("aliases", [])])
            if not target.data:
                target.data = _profile(entity)
            continue
        if len(matches) > 1:
            # Two series identities answer to this name: a person decides, nothing is merged.
            for candidate in matches:
                link = previous.get(candidate.id) or SeriesEntityLink(
                    series_entity_id=candidate.id, entity_id=entity.id, project_id=volume.id
                )
                if not link.human:
                    link.status, link.confidence = "proposed", 0.5
                    link.reason = "Plusieurs identités de la série portent ce nom : à confirmer"
                db.add(link)
            continue
        if not plausible_name(entity.name):
            continue
        created = SeriesEntity(
            series_id=series.id,
            name=entity.name[:300],
            category="character",
            aliases=[a for a in entity.data.get("aliases", []) if a != entity.name][:50],
            data=_profile(entity),
            first_project_id=volume.id,
            first_volume_number=volume.volume_number,
            first_position=first if isinstance(first := entity.data.get("first_position"), int) else -1,
            validated=bool(entity.validated or entity.identity_validated),
        )
        db.add(created)
        db.flush()
        registry.append(created)
        db.add(
            SeriesEntityLink(
                series_entity_id=created.id,
                entity_id=entity.id,
                project_id=volume.id,
                status="linked",
                confidence=1.0,
                reason="Première apparition dans la série",
            )
        )
    db.flush()


def _profile(entity: Entity) -> dict:
    keep = ("role", "description", "gender", "pronouns", "speech_style", "formal_or_informal")
    return {key: entity.data[key] for key in keep if entity.data.get(key)}


def _merged_aliases(target: SeriesEntity, values: list[str]) -> list[str]:
    known = {normalized(target.name)}
    aliases = []
    for value in [*target.aliases, *values]:
        if value and normalized(value) not in known:
            known.add(normalized(value))
            aliases.append(value[:300])
    return aliases[:50]


def link_world(db: Session, series: Series, volume: Project) -> None:
    """Places, organizations and objects named by a volume's bible become series identities."""
    for key, category in WORLD_LISTS.items():
        for value in (volume.bible or {}).get(key, []) or []:
            if not isinstance(value, str) or not value.strip():
                continue
            name = value.strip()[:300]
            existing = db.scalar(
                select(SeriesEntity).where(
                    SeriesEntity.series_id == series.id, SeriesEntity.category == category, SeriesEntity.name == name
                )
            )
            if existing:
                continue
            db.add(
                SeriesEntity(
                    series_id=series.id,
                    name=name,
                    category=category,
                    aliases=[],
                    data={},
                    first_project_id=volume.id,
                    first_volume_number=volume.volume_number,
                )
            )
            db.flush()


def link_relations(db: Session, series: Series, volume: Project) -> None:
    linked = {
        link.entity_id: link.series_entity_id
        for link in db.scalars(
            select(SeriesEntityLink).where(
                SeriesEntityLink.project_id == volume.id, SeriesEntityLink.status == "linked"
            )
        )
    }
    for relation in db.scalars(
        select(CharacterRelation)
        .where(CharacterRelation.project_id == volume.id, CharacterRelation.active.is_(True))
        .order_by(CharacterRelation.position)
    ):
        source, target = linked.get(relation.source_id), linked.get(relation.target_id)
        if not source or not target:
            continue
        source = canonical(db, db.get(SeriesEntity, source)).id
        target = canonical(db, db.get(SeriesEntity, target)).id
        if source == target:
            continue
        existing = db.scalar(
            select(SeriesRelation).where(
                SeriesRelation.series_id == series.id,
                SeriesRelation.source_id == source,
                SeriesRelation.target_id == target,
                SeriesRelation.relation_type == relation.relation_type,
            )
        )
        if existing:
            existing.validated = existing.validated or relation.validated
            continue
        db.add(
            SeriesRelation(
                series_id=series.id,
                source_id=source,
                target_id=target,
                relation_type=relation.relation_type,
                description=relation.description,
                evidence=relation.evidence,
                first_project_id=volume.id,
                first_volume_number=volume.volume_number,
                first_position=relation.position,
                validated=relation.validated,
            )
        )
        db.flush()


def aggregate_terms(db: Session, series: Series, volumes: list[Project]) -> None:
    """Accepted volume terms feed the series glossary; human series terms are never rewritten."""
    current = {
        term.source.casefold(): term
        for term in db.scalars(select(SeriesTerm).where(SeriesTerm.series_id == series.id))
    }
    for volume in volumes:
        for term in db.scalars(
            select(Glossary).where(
                Glossary.project_id == volume.id,
                Glossary.accepted.is_(True),
                Glossary.series_override.is_(False),
            )
        ):
            key = term.source.casefold()
            existing = current.get(key)
            if existing and existing.origin == "human":
                continue
            if existing is None:
                existing = SeriesTerm(
                    series_id=series.id,
                    source=term.source,
                    translation=term.translation,
                    category=term.category,
                    description=term.description,
                    locked=term.locked,
                    accepted=True,
                    origin="volume",
                    first_project_id=volume.id,
                    first_volume_number=volume.volume_number,
                )
                db.add(existing)
                current[key] = existing
                continue
            # Later volumes refine the translation unless an earlier one locked it.
            if term.locked or not existing.locked:
                existing.translation, existing.category = term.translation, term.category
                existing.description = term.description or existing.description
                existing.locked = existing.locked or term.locked
    db.flush()


def snapshot(db: Session, series: Series, volumes: list[Project]) -> dict:
    universe: dict = {}
    for volume in volumes:
        for key in STYLE_KEYS:
            value = (volume.bible or {}).get(key)
            if value:
                universe[key] = value
    world: dict[str, list] = {}
    for key, category in WORLD_LISTS.items():
        world[key] = [
            {"name": entity.name, "first_volume": entity.first_volume_number}
            for entity in db.scalars(
                select(SeriesEntity)
                .where(SeriesEntity.series_id == series.id, SeriesEntity.category == category)
                .order_by(SeriesEntity.first_volume_number, SeriesEntity.name)
            )
        ]
    conventions: dict[str, list] = {key: [] for key in CONVENTION_LISTS}
    for volume in volumes:
        for key in CONVENTION_LISTS:
            for value in (volume.bible or {}).get(key, []) or []:
                if value not in conventions[key]:
                    conventions[key].append(value)
    characters = [
        {
            "id": entity.id,
            "name": entity.name,
            "aliases": entity.aliases,
            "first_volume": entity.first_volume_number,
            "validated": entity.validated,
            **{key: entity.data.get(key) for key in ("role", "description") if entity.data.get(key)},
        }
        for entity in db.scalars(
            select(SeriesEntity)
            .where(
                SeriesEntity.series_id == series.id,
                SeriesEntity.category == "character",
                SeriesEntity.merged_into_id.is_(None),
            )
            .order_by(SeriesEntity.first_volume_number, SeriesEntity.first_position, SeriesEntity.name)
        )
    ]
    return {
        "schema_version": 1,
        "series": series.name,
        "universe": universe,
        **world,
        "conventions": conventions,
        "chronology": [
            {
                "project_id": volume.id,
                "volume": volume.volume_number,
                "title": volume.title,
                "summary": (volume.bible or {}).get("summary", ""),
            }
            for volume in volumes
            if volume.bible
        ],
        "characters": characters,
        "relations": len(db.scalars(select(SeriesRelation.id).where(SeriesRelation.series_id == series.id)).all()),
        "updated_at": time.time(),
    }


def refresh_series(db: Session, series_id: str | None) -> Series | None:
    """Recomputes the series memory from its volumes; safe to call after any change."""
    series = db.get(Series, series_id) if series_id else None
    if not series:
        return None
    from app.engines.ingestion.store import lock

    lock(db, f"series-memory:{series.id}")
    volumes = series_volumes(db, series.id)
    for volume in volumes:
        link_characters(db, series, volume)
        link_world(db, series, volume)
        link_relations(db, series, volume)
    aggregate_terms(db, series, volumes)
    series.authors = list(dict.fromkeys(v.author for v in volumes if v.author))[:20]
    pairs = {(v.source_language, v.target_language) for v in volumes}
    if len(pairs) == 1 and not series.source_language:
        series.source_language, series.target_language = next(iter(pairs))
    if not series.bible_validated:
        series.bible = snapshot(db, series, volumes)
    series.updated_at = time.time()
    db.flush()
    return series
