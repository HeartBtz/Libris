"""Glossary imports into a book, a series or a shared glossary, and the shared glossary of a series.

Precedence of a term, from the most specific level to the broadest: the book, then the series (a
person's series decisions, then earlier volumes), then the shared glossary the series follows. A
locked term of a broader level still beats an unlocked, automatic entry of a narrower one, unless a
person decided otherwise at that level (a book term marked as a deliberate override, or a series
decision); see app.engines.context.series.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engines.memory.glossary_files import COMPARED, parse_glossary, plan_import
from app.languages import primary
from app.models import Project, SeriesSharedGlossary, SharedGlossary, SharedTerm

MAX_GLOSSARY_BYTES = 2 * 1024**2


@dataclass
class ImportOptions:
    strategy: str = "skip"
    delimiter: str | None = None
    mapping: dict[str, int] | None = None
    header: bool | None = None
    # Leave out invalid rows instead of refusing the whole file (the preview lists them).
    skip_invalid: bool = False


def normalize_name(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def plan_file(
    db: Session,
    model,
    scope: dict,
    data: bytes,
    filename: str,
    source_language: str,
    target_language: str,
    options: ImportOptions,
    preview: bool = False,
) -> dict:
    """The import plan of a file against the terms in place in `scope` (column name → value).

    A preview always lists invalid rows instead of refusing the file; applying refuses it unless
    `skip_invalid` was chosen."""
    parsed = parse_glossary(
        data,
        filename,
        source_language,
        target_language,
        strict=not (options.skip_invalid or preview),
        delimiter=options.delimiter,
        mapping=options.mapping,
        header=options.header,
    )
    existing = [
        {"id": term.id, "source": term.source, **{name: getattr(term, name) for name in COMPARED}}
        for term in db.scalars(select(model).filter_by(**scope))
    ]
    return plan_import(existing, parsed, options.strategy)


def apply_plan(db: Session, model, scope: dict, plan: dict, extra: dict | None = None) -> list[str]:
    """Adds and replaces the terms the plan decided; returns the sources that changed."""
    changed = []
    for action, term_id, values in plan["actions"]:
        if action == "add":
            db.add(model(**scope, **values, **(extra or {})))
        else:
            term = db.get(model, term_id)
            if term is None:
                continue
            for name in COMPARED:
                setattr(term, name, values[name])
            for name, value in (extra or {}).items():
                setattr(term, name, value)
        changed.append(values["source"])
    db.flush()
    return changed


def public_report(plan: dict, applied: bool) -> dict:
    """The plan as the API answers it; `imported`/`skipped` keep the meaning they always had."""
    counts = plan["counts"]
    report = {key: value for key, value in plan.items() if key != "actions"}
    report["applied"] = applied
    if applied:
        report["imported"] = counts["new"]
        report["replaced"] = counts["replaced"]
        report["skipped"] = counts["terms"] - counts["new"] - counts["replaced"]
    return report


def attached_glossary(db: Session, series_id: str | None) -> SharedGlossary | None:
    if not series_id:
        return None
    link = db.get(SeriesSharedGlossary, series_id)
    return db.get(SharedGlossary, link.glossary_id) if link else None


def languages_match(
    glossary: SharedGlossary, source_language: str | None, target_language: str | None
) -> bool:
    """A glossary without languages fits any pair; otherwise the primary languages must agree."""
    for expected, actual in (
        (glossary.source_language, source_language),
        (glossary.target_language, target_language),
    ):
        if expected and actual and primary(expected) != primary(actual):
            return False
    return True


def shared_terms(db: Session, project: Project) -> tuple[SharedGlossary | None, list[SharedTerm]]:
    """The accepted terms of the shared glossary the project's series follows, locked first."""
    glossary = attached_glossary(db, project.series_id)
    if glossary is None or not languages_match(glossary, project.source_language, project.target_language):
        return None, []
    terms = db.scalars(
        select(SharedTerm)
        .where(SharedTerm.glossary_id == glossary.id, SharedTerm.accepted.is_(True))
        .order_by(SharedTerm.locked.desc(), SharedTerm.source)
    ).all()
    return glossary, list(terms)


def add_shared_terms(db: Session, project: Project | None, chosen: dict[str, dict]) -> list[dict]:
    """Completes the series choices with the shared glossary, the broadest level."""
    if project is None:
        return list(chosen.values())
    glossary, terms = shared_terms(db, project)
    for term in terms:
        key = term.source.casefold()
        entry = {
            "source": term.source,
            "translation": term.translation,
            "locked": term.locked,
            "source_volume": None,
            "source_project": glossary.name,
            "origin": "shared_glossary",
        }
        current = chosen.get(key)
        if current is None:
            chosen[key] = entry
        elif term.locked and not current["locked"] and current.get("origin") != "series_decision":
            # A locked universe term beats an unlocked term an earlier volume proposed.
            chosen[key] = entry
    return list(chosen.values())
