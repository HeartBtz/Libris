"""Shared glossaries, glossary file previews and imports at every level, and the effective glossary.

A shared glossary belongs to one account and gathers the terminology of a universe; each series of
that account can follow one. Imports (JSON, CSV, TBX) are previewed first: what is new, unchanged,
in conflict with a term in place, repeated in the file or unreadable; applying uses the same plan.
"""

import json
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from pydantic import Field
from sqlalchemy import func, select

from app.api.common import row
from app.api.series import series_access
from app.api.tokens import Caller, require
from app.engines.context.series import prior_volumes, series_terms
from app.engines.memory.glossaries import (
    MAX_GLOSSARY_BYTES,
    ImportOptions,
    apply_plan,
    attached_glossary,
    languages_match,
    normalize_name,
    plan_file,
    public_report,
    shared_terms,
)
from app.engines.memory.glossary_files import EXPORT_DELIMITERS, HEADERS, STRATEGIES, render_export
from app.engines.memory.store import invalidate_after_decision
from app.engines.series.audit import audit
from app.i18n import localize, preferred_language
from app.models import (
    Glossary,
    Project,
    Series,
    SeriesSharedGlossary,
    SeriesTerm,
    SharedGlossary,
    SharedTerm,
    User,
)
from app.schemas import GlossaryTerm, StrictModel
from app.security import DB, CurrentUser, access

router = APIRouter(prefix="/api")
v1_router = APIRouter(prefix="/api/v1")
EXPORT_FIELDS = ("source", "translation", "category", "description", "locked", "accepted")
Format = Literal["json", "csv", "tbx"]
Delimiter = Literal["comma", "semicolon", "tab"]


class SharedGlossaryInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    source_language: str | None = Field(default=None, max_length=80)
    target_language: str | None = Field(default=None, max_length=80)


class AttachInput(StrictModel):
    # None detaches the series from its shared glossary.
    glossary_id: str | None = None


def import_options(
    strategy: Annotated[str, Form()] = "skip",
    delimiter: Annotated[str | None, Form()] = None,
    mapping: Annotated[str | None, Form()] = None,
    header: Annotated[bool | None, Form()] = None,
    skip_invalid: Annotated[bool, Form()] = False,
) -> ImportOptions:
    if strategy not in STRATEGIES:
        raise HTTPException(
            422,
            {
                "code": "invalid_strategy",
                "message": "Stratégie d’import inconnue : skip, replace ou replace_all.",
            },
        )
    columns = None
    if mapping:
        try:
            columns = json.loads(mapping)
        except ValueError:
            columns = None
        if not isinstance(columns, dict) or not all(
            key in HEADERS and isinstance(value, int) and not isinstance(value, bool)
            for key, value in columns.items()
        ):
            raise HTTPException(
                422,
                {
                    "code": "invalid_mapping",
                    "message": "Correspondance de colonnes invalide : un objet {champ: numéro de colonne} est attendu.",
                },
            )
    return ImportOptions(
        strategy=strategy,
        delimiter=EXPORT_DELIMITERS.get(delimiter or "", delimiter),
        mapping=columns,
        header=header,
        skip_invalid=skip_invalid,
    )


Options = Annotated[ImportOptions, Depends(import_options)]


async def upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_GLOSSARY_BYTES + 1)
    if len(data) > MAX_GLOSSARY_BYTES:
        raise HTTPException(413, {"code": "glossary_too_large", "message": "Glossaire trop volumineux."})
    return data


def translated(report: dict, request: Request) -> dict:
    """Row messages of the report in the reader's language."""
    language = preferred_language(request.headers.get("accept-language"))
    report["errors"] = [{**item, "message": localize(item["message"], language)} for item in report["errors"]]
    return report


def export_response(
    values: list[dict], format: str, source: str, target: str, delimiter: str, bom: bool, name: str
):
    content, media_type = render_export(values, format, source or "und", target or "und", delimiter, bom)
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'},
    )


def exported(db, model, scope: dict) -> list[dict]:
    return [
        {name: getattr(term, name) for name in EXPORT_FIELDS}
        for term in db.scalars(select(model).filter_by(**scope).order_by(model.source))
    ]


# Book glossary: preview (the import itself stays at POST /api/projects/{pid}/glossary/import).


def book_import(
    db, project: Project, data: bytes, filename: str, options: ImportOptions, apply: bool
) -> dict:
    scope = {"project_id": project.id}
    plan = plan_file(
        db, Glossary, scope, data, filename, project.source_language, project.target_language, options,
        preview=not apply,
    )  # fmt: skip
    if apply:
        changed = apply_plan(db, Glossary, scope, plan)
        if changed:
            invalidate_after_decision(db, project)
        db.commit()
    return public_report(plan, apply)


@router.post("/projects/{pid}/glossary/import/preview")
async def preview_book_import(
    pid: str, file: UploadFile, options: Options, request: Request, user: CurrentUser, db: DB
):
    project = access(db, pid, user, write=True)
    data = await upload(file)
    return translated(book_import(db, project, data, file.filename or "", options, apply=False), request)


def level_entry(level: str, translation: str, locked: bool, origin: str, volume: int | None = None) -> dict:
    return {"level": level, "translation": translation, "locked": locked, "origin": origin, "volume": volume}


@router.get("/projects/{pid}/glossary/effective")
def effective_glossary(pid: str, user: CurrentUser, db: DB):
    """Every accepted term the pipeline applies to this book, with the level it comes from and what it
    overrides: the book, then the series, then the shared glossary, a locked term beating an unlocked one."""
    project = access(db, pid, user)
    book = {
        term.source.casefold(): term
        for term in db.scalars(
            select(Glossary).where(Glossary.project_id == pid, Glossary.accepted.is_(True))
        )
    }
    series = {
        term["source"].casefold(): term
        for term in series_terms(db, prior_volumes(db, project), project, shared=False)
    }
    glossary, universe = shared_terms(db, project)
    shared = {term.source.casefold(): term for term in universe}
    result = []
    for key in sorted(set(book) | set(series) | set(shared)):
        candidates = []
        if key in book:
            term = book[key]
            origin = "series_override" if term.series_override else "book"
            candidates.append((level_entry("book", term.translation, term.locked, origin), term.source))
        if key in series:
            term = series[key]
            entry = level_entry(
                "series",
                term["translation"],
                term["locked"],
                term.get("origin") or "volume",
                term["source_volume"],
            )
            candidates.append((entry, term["source"]))
        if key in shared:
            term = shared[key]
            candidates.append(
                (level_entry("shared", term.translation, term.locked, "shared_glossary"), term.source)
            )
        winner = pick_level(key, book, series, shared)
        entries = [entry for entry, _ in candidates]
        chosen = next(entry for entry in entries if entry["level"] == winner)
        result.append(
            {
                "source": next(source for entry, source in candidates if entry["level"] == winner),
                **chosen,
                "overridden": [entry for entry in entries if entry is not chosen],
            }
        )
    return {
        "shared_glossary": {"id": glossary.id, "name": glossary.name} if glossary else None,
        "terms": result,
    }


def pick_level(key: str, book: dict, series: dict, shared: dict) -> str:
    """The same precedence as the translation context (app.engines.context.builder)."""
    inherited = None
    if key in series:
        term = series[key]
        inherited = "series"
        if (
            key in shared
            and shared[key].locked
            and not term["locked"]
            and term.get("origin") != "series_decision"
        ):
            inherited = "shared"
    elif key in shared:
        inherited = "shared"
    if key in book:
        term = book[key]
        if term.locked or term.series_override or inherited is None:
            return "book"
        locked = series[key]["locked"] if inherited == "series" else shared[key].locked
        return inherited if locked else "book"
    return inherited or "book"


# Series glossary: files.


@router.get("/series/{series_id}/glossary/export/{format}")
def export_series_glossary(
    series_id: str,
    format: Format,
    user: CurrentUser,
    db: DB,
    delimiter: Delimiter = "comma",
    bom: bool = False,
):
    series = series_access(db, series_id, user)
    values = exported(db, SeriesTerm, {"series_id": series.id})
    return export_response(
        values, format, series.source_language, series.target_language, delimiter, bom, "series-glossary"
    )


async def series_import(
    series_id: str, file: UploadFile, options: ImportOptions, user: User, db, apply: bool
):
    series = series_access(db, series_id, user, owner=True)
    data = await upload(file)
    scope = {"series_id": series.id}
    plan = plan_file(
        db, SeriesTerm, scope, data, file.filename or "", series.source_language or "",
        series.target_language or "", options, preview=not apply,
    )  # fmt: skip
    if apply:
        # Imported terms are a person's decisions: the aggregation of volumes never rewrites them.
        apply_plan(db, SeriesTerm, scope, plan, extra={"origin": "human"})
        counts = plan["counts"]
        audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.glossary_imported",
              series_id=series.id, added=counts["new"], replaced=counts["replaced"], file=file.filename or "")  # fmt: skip
        db.commit()
    return public_report(plan, apply)


@router.post("/series/{series_id}/glossary/import/preview")
async def preview_series_import(
    series_id: str, file: UploadFile, options: Options, request: Request, user: CurrentUser, db: DB
):
    return translated(await series_import(series_id, file, options, user, db, apply=False), request)


@router.post("/series/{series_id}/glossary/import")
async def import_series_glossary(
    series_id: str, file: UploadFile, options: Options, request: Request, user: CurrentUser, db: DB
):
    return translated(await series_import(series_id, file, options, user, db, apply=True), request)


# Shared glossaries.


def owned(db, glossary_id: str, user: User) -> SharedGlossary:
    glossary = db.get(SharedGlossary, glossary_id)
    if not glossary or glossary.owner_id != user.id:
        raise HTTPException(404, {"code": "glossary_not_found", "message": "Glossaire partagé introuvable."})
    return glossary


def glossary_view(db, glossary: SharedGlossary, terms: bool = False) -> dict:
    counts = db.execute(
        select(func.count(), func.count().filter(SharedTerm.locked.is_(True))).where(
            SharedTerm.glossary_id == glossary.id
        )
    ).one()
    attached = db.execute(
        select(Series.id, Series.name)
        .join(SeriesSharedGlossary, SeriesSharedGlossary.series_id == Series.id)
        .where(SeriesSharedGlossary.glossary_id == glossary.id)
        .order_by(Series.name)
    ).all()
    view = {
        **row(glossary, ("normalized_name",)),
        "term_count": counts[0],
        "locked_count": counts[1],
        "series": [{"id": series_id, "name": name} for series_id, name in attached],
    }
    if terms:
        view["terms"] = [
            row(term)
            for term in db.scalars(
                select(SharedTerm).where(SharedTerm.glossary_id == glossary.id).order_by(SharedTerm.source)
            )
        ]
    return view


def unique_name(db, owner_id: str, name: str, glossary_id: str | None = None) -> str:
    normalized = normalize_name(name)
    if not normalized:
        raise HTTPException(422, {"code": "invalid_name", "message": "Le nom du glossaire est requis."})
    other = db.scalar(
        select(SharedGlossary).where(
            SharedGlossary.owner_id == owner_id, SharedGlossary.normalized_name == normalized
        )
    )
    if other and other.id != glossary_id:
        raise HTTPException(
            409, {"code": "glossary_exists", "message": f"Un glossaire partagé « {other.name} » existe déjà."}
        )
    return normalized


def create_glossary(db, user: User, body: SharedGlossaryInput) -> SharedGlossary:
    glossary = SharedGlossary(
        owner_id=user.id,
        name=" ".join(body.name.split()),
        normalized_name=unique_name(db, user.id, body.name),
        description=body.description,
        source_language=body.source_language or None,
        target_language=body.target_language or None,
    )
    db.add(glossary)
    db.flush()
    audit(db, owner_id=user.id, actor_id=user.id, action="shared_glossary.created", glossary_id=glossary.id,
          name=glossary.name)  # fmt: skip
    db.commit()
    return glossary


@router.get("/glossaries")
def list_glossaries(user: CurrentUser, db: DB):
    return [
        glossary_view(db, glossary)
        for glossary in db.scalars(
            select(SharedGlossary).where(SharedGlossary.owner_id == user.id).order_by(SharedGlossary.name)
        )
    ]


@router.post("/glossaries", status_code=201)
def add_glossary(body: SharedGlossaryInput, user: CurrentUser, db: DB):
    return glossary_view(db, create_glossary(db, user, body), terms=True)


@router.get("/glossaries/{glossary_id}")
def get_glossary(glossary_id: str, user: CurrentUser, db: DB):
    return glossary_view(db, owned(db, glossary_id, user), terms=True)


@router.put("/glossaries/{glossary_id}")
def edit_glossary(glossary_id: str, body: SharedGlossaryInput, user: CurrentUser, db: DB):
    glossary = owned(db, glossary_id, user)
    glossary.normalized_name = unique_name(db, user.id, body.name, glossary.id)
    glossary.name = " ".join(body.name.split())
    glossary.description = body.description
    glossary.source_language = body.source_language or None
    glossary.target_language = body.target_language or None
    db.commit()
    return glossary_view(db, glossary, terms=True)


@router.delete("/glossaries/{glossary_id}")
def delete_glossary(glossary_id: str, user: CurrentUser, db: DB):
    glossary = owned(db, glossary_id, user)
    for link in db.scalars(
        select(SeriesSharedGlossary).where(SeriesSharedGlossary.glossary_id == glossary.id)
    ):
        db.delete(link)
    audit(db, owner_id=user.id, actor_id=user.id, action="shared_glossary.deleted", glossary_id=glossary.id,
          name=glossary.name)  # fmt: skip
    db.delete(glossary)
    db.commit()
    return {"ok": True}


def shared_term(db, glossary: SharedGlossary, term_id: str) -> SharedTerm:
    term = db.get(SharedTerm, term_id)
    if not term or term.glossary_id != glossary.id:
        raise HTTPException(404, {"code": "term_not_found", "message": "Terme introuvable."})
    return term


@router.post("/glossaries/{glossary_id}/terms", status_code=201)
def add_shared_term(glossary_id: str, body: GlossaryTerm, user: CurrentUser, db: DB):
    glossary = owned(db, glossary_id, user)
    source = body.source.strip()
    existing = db.scalars(select(SharedTerm.source).where(SharedTerm.glossary_id == glossary.id))
    if source.casefold() in {value.casefold() for value in existing}:
        raise HTTPException(
            409, {"code": "term_exists", "message": "Ce terme existe déjà dans le glossaire partagé."}
        )
    term = SharedTerm(glossary_id=glossary.id, **(body.model_dump() | {"source": source}))
    db.add(term)
    glossary.updated_at = time.time()
    db.commit()
    return row(term)


@router.put("/glossaries/{glossary_id}/terms/{term_id}")
def edit_shared_term(glossary_id: str, term_id: str, body: GlossaryTerm, user: CurrentUser, db: DB):
    glossary = owned(db, glossary_id, user)
    term = shared_term(db, glossary, term_id)
    source = body.source.strip()
    clash = db.scalar(
        select(SharedTerm).where(
            SharedTerm.glossary_id == glossary.id, func.lower(SharedTerm.source) == source.lower(),
            SharedTerm.id != term.id,
        )
    )  # fmt: skip
    if clash:
        raise HTTPException(
            409, {"code": "term_exists", "message": "Ce terme existe déjà dans le glossaire partagé."}
        )
    for key, value in (body.model_dump() | {"source": source}).items():
        setattr(term, key, value)
    glossary.updated_at = time.time()
    db.commit()
    return row(term)


@router.delete("/glossaries/{glossary_id}/terms/{term_id}")
def delete_shared_term(glossary_id: str, term_id: str, user: CurrentUser, db: DB):
    glossary = owned(db, glossary_id, user)
    db.delete(shared_term(db, glossary, term_id))
    glossary.updated_at = time.time()
    db.commit()
    return {"ok": True}


@router.get("/glossaries/{glossary_id}/export/{format}")
def export_shared_glossary(
    glossary_id: str,
    format: Format,
    user: CurrentUser,
    db: DB,
    delimiter: Delimiter = "comma",
    bom: bool = False,
):
    glossary = owned(db, glossary_id, user)
    values = exported(db, SharedTerm, {"glossary_id": glossary.id})
    return export_response(
        values, format, glossary.source_language, glossary.target_language, delimiter, bom, "shared-glossary"
    )


async def shared_import(
    glossary: SharedGlossary, file: UploadFile, options: ImportOptions, user: User, db, apply: bool
):
    data = await upload(file)
    scope = {"glossary_id": glossary.id}
    plan = plan_file(
        db, SharedTerm, scope, data, file.filename or "", glossary.source_language or "",
        glossary.target_language or "", options, preview=not apply,
    )  # fmt: skip
    if apply:
        apply_plan(db, SharedTerm, scope, plan)
        glossary.updated_at = time.time()
        counts = plan["counts"]
        audit(db, owner_id=glossary.owner_id, actor_id=user.id, action="shared_glossary.imported",
              glossary_id=glossary.id, added=counts["new"], replaced=counts["replaced"], file=file.filename or "")  # fmt: skip
        db.commit()
    return public_report(plan, apply)


@router.post("/glossaries/{glossary_id}/import/preview")
async def preview_shared_import(
    glossary_id: str, file: UploadFile, options: Options, request: Request, user: CurrentUser, db: DB
):
    glossary = owned(db, glossary_id, user)
    return translated(await shared_import(glossary, file, options, user, db, apply=False), request)


@router.post("/glossaries/{glossary_id}/import")
async def import_shared_glossary(
    glossary_id: str, file: UploadFile, options: Options, request: Request, user: CurrentUser, db: DB
):
    glossary = owned(db, glossary_id, user)
    return translated(await shared_import(glossary, file, options, user, db, apply=True), request)


# A series and the shared glossary it follows.


def series_link_view(db, series: Series) -> dict:
    glossary = attached_glossary(db, series.id)
    if glossary is None:
        return {"glossary": None}
    return {"glossary": glossary_view(db, glossary, terms=True)}


def attach(db, series: Series, glossary_id: str | None, user: User) -> dict:
    link = db.get(SeriesSharedGlossary, series.id)
    if glossary_id is None:
        if link:
            audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.shared_glossary_detached",
                  series_id=series.id, glossary_id=link.glossary_id)  # fmt: skip
            db.delete(link)
            db.commit()
        return {"glossary": None}
    glossary = owned(db, glossary_id, user)
    if not languages_match(glossary, series.source_language, series.target_language):
        raise HTTPException(
            409,
            {
                "code": "language_mismatch",
                "message": "Les langues de ce glossaire partagé ne correspondent pas à celles de la série.",
            },
        )
    if link:
        link.glossary_id = glossary.id
    else:
        db.add(SeriesSharedGlossary(series_id=series.id, glossary_id=glossary.id))
    audit(db, owner_id=series.owner_id, actor_id=user.id, action="series.shared_glossary_attached",
          series_id=series.id, glossary_id=glossary.id, name=glossary.name)  # fmt: skip
    db.commit()
    return series_link_view(db, series)


@router.get("/series/{series_id}/shared-glossary")
def series_shared_glossary(series_id: str, user: CurrentUser, db: DB):
    return series_link_view(db, series_access(db, series_id, user))


@router.put("/series/{series_id}/shared-glossary")
def attach_shared_glossary(series_id: str, body: AttachInput, user: CurrentUser, db: DB):
    return attach(db, series_access(db, series_id, user, owner=True), body.glossary_id, user)


# Automation API (/api/v1): the same operations for API tokens.

Reader = Annotated[Caller, Depends(require("series:read"))]
Writer = Annotated[Caller, Depends(require("content:write"))]


def owned_series(db, series_id: str, user: User) -> Series:
    series = db.get(Series, series_id)
    if not series or series.owner_id != user.id:
        raise HTTPException(404, {"code": "series_not_found", "message": "Série introuvable."})
    return series


@v1_router.get("/glossaries")
def v1_list_glossaries(caller: Reader, db: DB):
    """The caller's shared glossaries, with their term counts and the series that follow them."""
    return list_glossaries(caller.user, db)


@v1_router.post("/glossaries", status_code=201)
def v1_add_glossary(body: SharedGlossaryInput, caller: Writer, db: DB):
    return glossary_view(db, create_glossary(db, caller.user, body), terms=True)


@v1_router.get("/glossaries/{glossary_id}")
def v1_get_glossary(glossary_id: str, caller: Reader, db: DB):
    return glossary_view(db, owned(db, glossary_id, caller.user), terms=True)


@v1_router.get("/glossaries/{glossary_id}/export/{format}")
def v1_export_glossary(
    glossary_id: str,
    format: Format,
    caller: Reader,
    db: DB,
    delimiter: Delimiter = "comma",
    bom: bool = False,
):
    return export_shared_glossary(glossary_id, format, caller.user, db, delimiter, bom)


@v1_router.post("/glossaries/{glossary_id}/import")
async def v1_import_glossary(
    glossary_id: str,
    file: UploadFile,
    options: Options,
    request: Request,
    caller: Writer,
    db: DB,
    dry_run: bool = False,
):
    """Imports a JSON, CSV or TBX file; `?dry_run=true` answers the preview without changing anything."""
    glossary = owned(db, glossary_id, caller.user)
    try:
        report = await shared_import(glossary, file, options, caller.user, db, apply=not dry_run)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid_glossary", "message": str(exc)[:1500]}) from None
    return translated(report, request)


@v1_router.get("/series/{series_id}/shared-glossary")
def v1_series_shared_glossary(series_id: str, caller: Reader, db: DB):
    return series_link_view(db, owned_series(db, series_id, caller.user))


@v1_router.put("/series/{series_id}/shared-glossary")
def v1_attach_shared_glossary(series_id: str, body: AttachInput, caller: Writer, db: DB):
    return attach(db, owned_series(db, series_id, caller.user), body.glossary_id, caller.user)
