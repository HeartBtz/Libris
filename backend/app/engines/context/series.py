"""Conventions inherited from the earlier volumes of a series; nothing from a later volume ever leaks."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.epub.text import plain
from app.languages import primary
from app.models import Glossary, Memory, Project

MAX_DECISIONS = 12
COMMON_CAPITALS = {
    "a", "an", "and", "but", "he", "her", "his", "how", "i", "it", "its", "mr", "mrs", "ms", "no", "oh",
    "she", "that", "the", "their", "then", "they", "this", "we", "what", "when", "where", "who", "why",
    "yes", "you", "your",
}  # fmt: skip


@dataclass
class SeriesTerm:
    """Duck-types a Glossary row for the output checks."""

    source: str
    translation: str
    locked: bool = True


def series_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def prior_volumes(db: Session, project: Project) -> list[Project]:
    """Earlier volumes of the same series, same owner and language pair, most recent first."""
    key = series_key(project.series_name)
    if not key or not project.volume_number:
        return []
    candidates = db.scalars(
        select(Project)
        .where(
            Project.owner_id == project.owner_id,
            Project.id != project.id,
            Project.series_name != "",
            Project.volume_number.is_not(None),
            Project.volume_number < project.volume_number,
        )
        .order_by(Project.volume_number.desc(), Project.created_at.desc())
    )
    # "Saga" and "saga ", "en" and "en-US" name the same series and language: an exact SQL match
    # silently cut a volume off its predecessors.
    return [
        candidate
        for candidate in candidates
        if series_key(candidate.series_name) == key
        and primary(candidate.source_language) == primary(project.source_language)
        and primary(candidate.target_language) == primary(project.target_language)
    ]


def series_terms(db: Session, prior: list[Project]) -> list[dict]:
    """One entry per source term: a locked choice beats any unlocked one, then the latest volume wins."""
    if not prior:
        return []
    volumes = {candidate.id: candidate for candidate in prior}
    rank = {candidate.id: index for index, candidate in enumerate(prior)}
    terms = db.scalars(
        select(Glossary).where(Glossary.project_id.in_(volumes), Glossary.accepted.is_(True))
    ).all()
    terms = sorted(terms, key=lambda term: (not term.locked, rank[term.project_id], -term.created_at))
    chosen: dict[str, dict] = {}
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
    return list(chosen.values())


def enforced_glossary(db: Session, project: Project) -> list:
    """The book's accepted glossary plus the locked series terms it does not lock differently itself."""
    local = list(
        db.scalars(select(Glossary).where(Glossary.project_id == project.id, Glossary.accepted.is_(True)))
    )
    locked_here = {term.source.casefold() for term in local if term.locked}
    inherited = [
        SeriesTerm(term["source"], term["translation"])
        for term in series_terms(db, prior_volumes(db, project))
        if term["locked"] and term["source"].casefold() not in locked_here
    ]
    return [*local, *inherited]


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
        matcher = SequenceMatcher(None, [t[0] for t in old_tokens], [t[0] for t in new_tokens], autojunk=False)
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
