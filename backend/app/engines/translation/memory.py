"""Translation memory: a passage already translated identically is reused instead of paying the model."""

import hashlib
import json
import unicodedata

from sqlalchemy import select

from app.db import SessionLocal
from app.engines.context.series import enforced_glossary, prior_volumes, series_key
from app.engines.quality.checks import checks, locked_term_error, validate_translation
from app.languages import primary
from app.models import Project, Segment
from app.schemas import TranslationResult


def memory_key(units: list[dict]) -> str:
    """Identity of a passage's source: Unicode compatibility forms and spacing do not matter, markers do."""
    normalized = [" ".join(unicodedata.normalize("NFKC", unit["text"]).split()) for unit in units]
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()


def translation_memory_enabled(project: Project) -> bool:
    return bool((project.config or {}).get("translation_memory", True))


def remembered_translation(project: Project, segment: Segment) -> TranslationResult | None:
    """The best earlier translation of the same source for this owner and language pair, if any.

    A human-validated version comes first. In a series only this book and earlier volumes qualify,
    so that a later volume never leaks into an earlier one; outside a series, any book of the owner.
    """
    if not segment.source_key:
        return None
    with SessionLocal() as db:
        project = db.get(Project, project.id)
        if not translation_memory_enabled(project):
            return None
        allowed = None
        if series_key(project.series_name) and project.volume_number:
            allowed = {project.id, *(volume.id for volume in prior_volumes(db, project))}
        candidates = db.execute(
            select(Segment, Project)
            .join(Project, Segment.project_id == Project.id)
            .where(
                Segment.source_key == segment.source_key,
                Segment.id != segment.id,
                Segment.translation != "",
                Segment.retained_source.is_(False),
                Segment.status.not_in(("error", "refused", "blocked")),
                Segment.human.is_(True) | (Segment.stage == "done"),
                Project.owner_id == project.owner_id,
            )
            .order_by(
                Segment.validated.desc(),
                Segment.human.desc(),
                (Segment.project_id == project.id).desc(),
                Segment.created_at.desc(),
            )
            .limit(20)
        ).all()
        glossary = None
        for source, book in candidates:
            if allowed is not None and book.id not in allowed:
                continue
            if primary(book.source_language) != primary(project.source_language):
                continue
            if book.target_language.casefold() != project.target_language.casefold():
                continue
            if len(source.translated_units) != len(segment.units):
                continue
            result = TranslationResult(
                units=[
                    {"id": unit["id"], "text": translated["text"]}
                    for unit, translated in zip(segment.units, source.translated_units, strict=True)
                ]
            )
            try:
                validate_translation(segment.units, result)
            except ValueError:
                continue  # same words, different formatting: the markers cannot be carried over
            glossary = glossary if glossary is not None else enforced_glossary(db, project)
            findings = checks(
                segment.units,
                [u.model_dump() for u in result.units],
                glossary,
                project.source_language,
                project.target_language,
            )
            if locked_term_error(findings):
                continue  # this book locked another name since
            return result
    return None
