"""Conventions inherited from the earlier volumes of a series; nothing from a later volume ever leaks."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.epub.text import plain
from app.engines.memory.glossaries import add_shared_terms
from app.languages import primary
from app.models import Entity, Glossary, Memory, Project, SeriesEntity, SeriesEntityLink, SeriesTerm

MAX_DECISIONS = 12
COMMON_CAPITALS = {
    "a", "an", "and", "but", "he", "her", "his", "how", "i", "it", "its", "mr", "mrs", "ms", "no", "oh",
    "she", "that", "the", "their", "then", "they", "this", "we", "what", "when", "where", "who", "why",
    "yes", "you", "your",
}  # fmt: skip


@dataclass
class InheritedTerm:
    """Duck-types a Glossary row for the output checks."""

    source: str
    translation: str
    locked: bool = True
    # series (its glossary or an earlier volume) | shared_glossary
    origin: str = "series"


def series_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def prior_volumes(db: Session, project: Project) -> list[Project]:
    """Earlier volumes of the same series, same owner and language pair, most recent first.

    Only numbered volumes have an "earlier": a continuous webnovel container or an unnumbered volume
    relies on its own chapters, which the context already reads in order.
    """
    if not project.series_id or not project.volume_number:
        return []
    candidates = db.scalars(
        select(Project)
        .where(
            Project.series_id == project.series_id,
            Project.owner_id == project.owner_id,
            Project.id != project.id,
            Project.volume_number.is_not(None),
            Project.volume_number < project.volume_number,
        )
        .order_by(Project.volume_number.desc(), Project.created_at.desc())
    )
    # "en" and "en-US" name the same language: an exact match silently cut a volume off its predecessors.
    return [
        candidate
        for candidate in candidates
        if primary(candidate.source_language) == primary(project.source_language)
        and primary(candidate.target_language) == primary(project.target_language)
    ]


def series_terms(
    db: Session, prior: list[Project], project: Project | None = None, shared: bool = True
) -> list[dict]:
    """One entry per source term: a person's series decision first, then a locked choice beats any
    unlocked one, then the latest volume wins. Nothing a later volume introduced is ever offered.
    The shared glossary the series follows comes last (app.engines.memory.glossaries)."""
    chosen: dict[str, dict] = {}
    if project is not None and project.series_id:
        for term in db.scalars(
            select(SeriesTerm)
            .where(
                SeriesTerm.series_id == project.series_id,
                SeriesTerm.origin == "human",
                SeriesTerm.accepted.is_(True),
            )
            .order_by(SeriesTerm.locked.desc(), SeriesTerm.updated_at.desc())
        ):
            chosen.setdefault(
                term.source.casefold(),
                {
                    "source": term.source,
                    "translation": term.translation,
                    "locked": term.locked,
                    "source_volume": None,
                    "source_project": "series",
                    "origin": "series_decision",
                },
            )
    if not prior:
        return add_shared_terms(db, project if shared else None, chosen)
    volumes = {candidate.id: candidate for candidate in prior}
    rank = {candidate.id: index for index, candidate in enumerate(prior)}
    terms = db.scalars(
        select(Glossary).where(Glossary.project_id.in_(volumes), Glossary.accepted.is_(True))
    ).all()
    terms = sorted(terms, key=lambda term: (not term.locked, rank[term.project_id], -term.created_at))
    for term in terms:
        chosen.setdefault(
            term.source.casefold(),
            {
                "source": term.source,
                "translation": term.translation,
                "locked": term.locked,
                "source_volume": volumes[term.project_id].volume_number,
                "source_project": volumes[term.project_id].title,
            },
        )
    return add_shared_terms(db, project if shared else None, chosen)


def enforced_glossary(db: Session, project: Project) -> list:
    """The book's accepted glossary plus the locked series terms it does not decide otherwise itself.

    A volume term locked, or marked as a deliberate override of the series, wins over the series.
    """
    local = list(
        db.scalars(select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True)))
    )
    decided_here = {term.source.casefold() for term in local if term.locked or term.series_override}
    inherited = [
        InheritedTerm(term["source"], term["translation"], origin=term.get("origin") or "series")
        for term in series_terms(db, prior_volumes(db, project), project)
        if term["locked"] and term["source"].casefold() not in decided_here
    ]
    return [*local, *inherited]


def series_identities(db: Session, project: Project, prior: list[Project]) -> list[dict]:
    """Characters already met in earlier volumes, with only the names those volumes used for them."""
    if not prior:
        return []
    volume_ids = [volume.id for volume in prior]
    rows = db.execute(
        select(SeriesEntity, Entity)
        .join(SeriesEntityLink, SeriesEntityLink.series_entity_id == SeriesEntity.id)
        .join(Entity, Entity.id == SeriesEntityLink.entity_id)
        .where(
            SeriesEntityLink.project_id.in_(volume_ids),
            SeriesEntityLink.status == "linked",
            SeriesEntity.series_id == project.series_id,
        )
    ).all()
    found: dict[str, dict] = {}
    for series_entity, entity in rows:
        target = series_entity
        entry = found.setdefault(
            target.merged_into_id or target.id,
            {"canonical_name": target.name, "aliases": [], "validated": target.validated},
        )
        for name in [entity.name, *entity.data.get("aliases", [])]:
            if name and name != entry["canonical_name"] and name not in entry["aliases"]:
                entry["aliases"].append(name)
    return list(found.values())


def anchors(text: str) -> list[str]:
    """Names in a source passage: what a later volume can recognise a decision by."""
    found = []
    for match in re.finditer(r"(?<![\w’'])[A-Z][\w’'-]+(?:[ \t]+[A-Z][\w’'-]+)*", text):
        words = match.group().split()
        while words and words[0].casefold() in COMMON_CAPITALS:
            words.pop(0)
        if words and len(" ".join(words)) >= 3:
            found.append(" ".join(words))
    found += re.findall(r"[゠-ヿ]{2,}", text)
    return list(dict.fromkeys(found))[:10]


def tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\w+|[^\w\s]", text)]


def human_choices(source_units: list[dict], before: list[dict], after: list[dict]) -> dict:
    """What a person changed in a machine translation: short before/after pairs and the names nearby.

    A whole validated passage says nothing reusable to the next volume unless the same passage
    recurs; the corrected wording does.
    """
    previous = {unit["id"]: plain(unit["text"]) for unit in before}
    choices, names = [], []
    for source, unit in zip(source_units, after, strict=False):
        old, new = previous.get(unit["id"]), plain(unit["text"])
        if not old or old == new:
            continue
        old_tokens, new_tokens = tokens(old), tokens(new)
        matcher = SequenceMatcher(
            None, [t[0] for t in old_tokens], [t[0] for t in new_tokens], autojunk=False
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace" or i2 - i1 > 8 or j2 - j1 > 8:
                continue
            choices.append(
                {
                    "before": old[old_tokens[i1][1] : old_tokens[i2 - 1][2]],
                    "after": new[new_tokens[j1][1] : new_tokens[j2 - 1][2]],
                }
            )
        names += anchors(plain(source["text"]))
    unique = list({(c["before"], c["after"]): c for c in choices}.values())
    if not unique:
        return {}
    return {"choices": unique[:MAX_DECISIONS], "anchors": list(dict.fromkeys(names))[:10]}


def series_decisions(db: Session, prior: list[Project], local_source: str, used: set[str], mentioned) -> list:
    if not prior:
        return []
    volumes = {candidate.id: candidate for candidate in prior}
    rank = {candidate.id: index for index, candidate in enumerate(prior)}
    memories = sorted(
        db.scalars(
            select(Memory).where(
                Memory.project_id.in_(volumes), Memory.kind == "human_decision", Memory.validated.is_(True)
            )
        ),
        key=lambda memory: (rank[memory.project_id], -memory.created_at),
    )
    decisions = []
    for memory in memories:
        volume = volumes[memory.project_id]
        origin = {"source_volume": volume.volume_number, "source_project": volume.title}
        if memory.content.get("choices"):
            names = [name for name in memory.content.get("anchors", []) if mentioned(name, local_source)]
            if names:
                decisions.append({"about": names, "choices": memory.content["choices"], **origin})
        else:
            # Decisions recorded before choices were extracted: only an identical passage can use them.
            source = str(memory.content.get("source", ""))
            if source and source.casefold() not in used and mentioned(source, local_source):
                decisions.append(
                    {"source": source, "translation": memory.content.get("translation", ""), **origin}
                )
                used.add(source.casefold())
        if len(decisions) >= MAX_DECISIONS:
            break
    return decisions
