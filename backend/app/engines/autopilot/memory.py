"""Memory proposals the autopilot decides instead of a person: glossary terms, series identity links, the
Book Bible and chapters whose context became outdated.

No model call: each decision rests on evidence already in the database, turned into a confidence and
compared with a threshold (settings). Every decision is logged; human decisions are never revisited.
"""

from collections import defaultdict

from sqlalchemy import func, select

from app.automation_settings import autopilot_config
from app.db import SessionLocal
from app.engines.autopilot.decisions import record
from app.engines.memory.identities import names, normalized
from app.jobs import segment_state as state
from app.jobs.concurrency import job_lock
from app.jobs.queue import fence
from app.models import (
    Chapter,
    Entity,
    Glossary,
    Job,
    Memory,
    Project,
    Segment,
    SeriesEntity,
    SeriesEntityLink,
)


def decide_memory(job: Job, owner: str, *, translated: bool = False) -> None:
    """After the analysis (and again once the book is translated, with `translated`)."""
    decide_glossary(job, owner)
    decide_identities(job, owner)
    if translated:
        decide_stale_chapters(job, owner)
    else:
        decide_bible(job, owner)


def term_confidence(occurrences: int) -> float:
    """A term the text never uses was imagined; one used once needs no enforcement; repeated, it does."""
    return 0.0 if occurrences <= 0 else min(1.0, 0.4 + 0.2 * occurrences)


def decide_glossary(job: Job, owner: str) -> None:
    threshold = autopilot_config()["glossary_min_confidence"]
    with job_lock(job.id), SessionLocal() as db:
        fence(db, job.id, owner)
        pending = list(
            db.scalars(
                select(Glossary).where(Glossary.project_id == job.project_id, Glossary.accepted.is_(False))
            )
        )
        if not pending:
            return
        text = "\n".join(db.scalars(select(Segment.source).where(Segment.project_id == job.project_id)))
        text = text.casefold()
        for term in pending:
            occurrences = text.count(term.source.casefold())
            confidence = term_confidence(occurrences)
            evidence = f"{occurrences} occurrence(s) dans le texte source, confiance {confidence:.2f}"
            if confidence >= threshold:
                term.accepted = True
                action, reason = "accepted", f"{evidence} ≥ {threshold:.2f}."
            else:
                db.delete(term)
                action, reason = "rejected", f"{evidence} < {threshold:.2f} ; proposition retirée."
            record(
                db,
                job.project_id,
                job_id=job.id,
                stage="memory",
                kind="glossary_term",
                action=action,
                reason=f"« {term.source} » → « {term.translation} » : {reason}",
            )
        db.commit()


def identity_confidence(entity: Entity, candidate: SeriesEntity) -> float:
    """Share of names in common, and a bonus when the profiles agree on the gender."""
    own = names(entity) or {normalized(entity.name)}
    theirs = {normalized(value) for value in [candidate.name, *candidate.aliases] if value}
    score = len(own & theirs) / max(1, len(own | theirs))
    gender, other = entity.data.get("gender", ""), candidate.data.get("gender", "")
    if gender and other:
        score += 0.2 if normalized(gender) == normalized(other) else -0.3
    return max(0.0, min(1.0, score))


def decide_identities(job: Job, owner: str) -> None:
    """Ambiguous series links: the clearly best candidate is linked; on doubt none is (never a merge)."""
    threshold = autopilot_config()["identity_min_confidence"]
    with job_lock(job.id), SessionLocal() as db:
        fence(db, job.id, owner)
        proposed = list(
            db.scalars(
                select(SeriesEntityLink).where(
                    SeriesEntityLink.project_id == job.project_id,
                    SeriesEntityLink.status == "proposed",
                    SeriesEntityLink.human.is_(False),
                )
            )
        )
        if not proposed:
            return
        groups: dict[str, list[SeriesEntityLink]] = defaultdict(list)
        for link in proposed:
            groups[link.entity_id].append(link)
        for entity_id, links in groups.items():
            entity = db.get(Entity, entity_id)
            scored = sorted(
                (
                    (identity_confidence(entity, db.get(SeriesEntity, link.series_entity_id)), link)
                    for link in links
                ),
                key=lambda pair: pair[0],
                reverse=True,
            )
            best, chosen = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else 0.0
            clear = best >= threshold and best - runner_up >= 0.1
            for score, link in scored:
                link.status = "linked" if clear and link is chosen else "rejected"
                link.confidence = round(score, 3)
                link.reason = "Autopilote : " + (
                    "meilleure correspondance" if link.status == "linked" else "correspondance insuffisante"
                )
            target = db.get(SeriesEntity, chosen.series_entity_id)
            record(
                db,
                job.project_id,
                job_id=job.id,
                stage="memory",
                kind="series_identity",
                action="linked" if clear else "rejected",
                reason=(
                    f"« {entity.name} » rattaché à « {target.name} » (confiance {best:.2f} ≥ {threshold:.2f}, "
                    f"écart {best - runner_up:.2f})."
                    if clear
                    else f"« {entity.name} » : {len(scored)} identités possibles, meilleure confiance "
                    f"{best:.2f} (seuil {threshold:.2f}, écart {best - runner_up:.2f}) ; personnage gardé "
                    "propre à ce volume plutôt que confondu."
                ),
            )
        db.commit()


def decide_bible(job: Job, owner: str) -> None:
    """The Book Bible is validated once enough of the book was analysed to build it."""
    threshold = autopilot_config()["bible_min_coverage"]
    with job_lock(job.id), SessionLocal() as db:
        fence(db, job.id, owner)
        project = db.get(Project, job.project_id)
        if project.bible_validated or not project.bible:
            return
        total = db.scalar(select(func.count(Segment.id)).where(Segment.project_id == project.id)) or 0
        analyzed = db.scalar(
            select(func.count(func.distinct(Memory.segment_id))).where(
                Memory.project_id == project.id, Memory.kind == "analysis"
            )
        )
        coverage = analyzed / total if total else 0.0
        validated = coverage >= threshold
        project.bible_validated = validated
        record(
            db,
            project.id,
            job_id=job.id,
            stage="memory",
            kind="book_bible",
            action="validated" if validated else "left_unvalidated",
            reason=f"{analyzed}/{total} passages analysés (couverture {coverage:.2f}, seuil {threshold:.2f}).",
        )
        db.commit()


def decide_stale_chapters(job: Job, owner: str) -> None:
    """A chapter flagged for an outdated context is cleared once this job translated or reviewed it again."""
    threshold = autopilot_config()["stale_min_coverage"]
    with job_lock(job.id), SessionLocal() as db:
        fence(db, job.id, owner)
        stale = list(
            db.scalars(
                select(Chapter).where(Chapter.project_id == job.project_id, Chapter.context_stale.is_(True))
            )
        )
        if not stale:
            return
        seen = state.marked(db, job.id, state.FINISHED) | state.marked(db, job.id, state.REVIEWED)
        for chapter in stale:
            ids = set(db.scalars(select(Segment.id).where(Segment.chapter_id == chapter.id)))
            coverage = len(ids & seen) / len(ids) if ids else 1.0
            cleared = coverage >= threshold
            chapter.context_stale = not cleared
            record(
                db,
                job.project_id,
                job_id=job.id,
                stage="memory",
                kind="context_stale",
                action="cleared" if cleared else "kept",
                reason=f"« {chapter.title} » : {coverage:.0%} des passages retraduits ou relus par ce travail "
                f"(seuil {threshold:.0%}).",
            )
        db.commit()
