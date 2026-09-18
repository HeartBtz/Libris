from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.common import row
from app.config import settings
from app.engines.context.config import memory_config
from app.engines.memory.catalog import CATALOG_NAME, catalog_uri, queue_catalog
from app.engines.memory.glossary_files import export_csv, export_json, export_tbx, read_glossary
from app.engines.memory.identities import canonical_bible, identities, names, normalized, upsert_profiles
from app.engines.memory.store import invalidate_after_decision
from app.models import AppSetting, BibleRevision, Entity, Glossary, Outbox, Prompt
from app.providers.openviking import OpenVikingClient, project_uri, validate_root
from app.providers.search import search_config
from app.schemas import BookBible, Character, GlossaryInput, ProviderInput
from app.security import DB, Admin, CurrentUser, access, encrypt

router = APIRouter(prefix="/api")


class SearchConfigInput(BaseModel):
    base_url: str = Field(default="", max_length=2000)
    enabled: bool = False


def validate_search_config(body: SearchConfigInput) -> dict:
    url = body.base_url.strip().rstrip("/")
    parts = urlsplit(url)
    if url and (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment):
        raise HTTPException(422, "URL HTTP(S) sans identifiants, paramètres ou fragment requise.")
    if body.enabled and not url:
        raise HTTPException(422, "Renseignez l’URL avant d’activer SearXNG.")
    return {"base_url": url, "enabled": body.enabled}


@router.get("/settings/searxng")
def get_search_config(user: Admin):
    return search_config()


@router.put("/settings/searxng")
def save_search_config(body: SearchConfigInput, user: Admin, db: DB):
    data = validate_search_config(body)
    saved = db.get(AppSetting, "searxng")
    if saved:
        saved.value = data
    else:
        db.add(AppSetting(key="searxng", value=data))
    db.commit()
    return data


@router.post("/settings/searxng/test")
async def test_search_config(body: SearchConfigInput, user: Admin):
    config = validate_search_config(body)
    if not config["base_url"]:
        raise HTTPException(422, "Renseignez une URL à tester.")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(config["base_url"] + "/search", params={"q": "dictionary", "format": "json"})
            if response.status_code == 403:
                raise HTTPException(422, "SearXNG refuse la requête (403). Activez search.formats: [html, json] et vérifiez les restrictions d’accès.")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError("Invalid search response")
            return {"ok": True, "results": len(data["results"]), "message": "API JSON SearXNG accessible."}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(422, "Test SearXNG échoué : vérifiez l’URL, le réseau et le format JSON.") from None


@router.get("/projects/{pid}/bible")
def bible(pid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user)
    return {
        "bible": canonical_bible(db, project),
        "validated": project.bible_validated,
        "entities": [row(e) for e in identities(db, pid)],
        "history": [
            row(v)
            for v in db.scalars(
                select(BibleRevision)
                .where(BibleRevision.project_id == pid)
                .order_by(BibleRevision.created_at.desc())
                .limit(30)
            )
        ],
    }


@router.put("/projects/{pid}/bible")
def update_bible(pid: str, body: BookBible, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    if not body.summary.strip():
        raise HTTPException(422, "Un résumé global non vide est nécessaire pour valider la Book Bible.")
    project.bible, project.bible_validated = body.model_dump(), True
    upsert_profiles(db, pid, [c.model_dump() for c in body.characters], -1, human=True)
    db.add(BibleRevision(project_id=pid, content=project.bible, human=True))
    invalidate_after_decision(db, project)
    db.commit()
    return project.bible


@router.put("/projects/{pid}/characters/{eid}")
def character(pid: str, eid: str, body: Character, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    entity = db.get(Entity, eid)
    if not entity or entity.project_id != pid:
        raise HTTPException(404, "Personnage introuvable.")
    if entity.merged_into_id:
        raise HTTPException(
            409, "Cette fiche a été fusionnée. Rechargez sa fiche canonique avant de la modifier."
        )
    name = body.canonical_name.strip()
    labels = [name, *body.aliases, *body.proposed_aliases]
    if not name or any(len(label) > 300 for label in labels):
        raise HTTPException(422, "Nom canonique requis ; noms et alias limités à 300 caractères.")
    claimed = {normalized(label) for label in [name, *body.aliases] if label.strip()}
    for other in identities(db, pid):
        if other.id != eid and claimed & names(other):
            raise HTTPException(
                409, "Ce nom ou cet alias désigne déjà une autre fiche. Utilisez la fusion d’identités."
            )
    entity.data = dict(
        body.model_dump(), canonical_name=name, first_position=entity.data.get("first_position", -1)
    )
    entity.name, entity.validated = name, True
    entity.identity_validated = True
    invalidate_after_decision(db, project, entity.name)
    db.commit()
    return row(entity)


@router.get("/projects/{pid}/glossary")
def glossary(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user)
    return [
        row(g)
        for g in db.scalars(select(Glossary).where(Glossary.project_id == pid).order_by(Glossary.source))
    ]


@router.post("/projects/{pid}/glossary")
def add_term(pid: str, body: GlossaryInput, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    term = Glossary(project_id=pid, **body.model_dump())
    db.add(term)
    invalidate_after_decision(db, project, term.source)
    db.commit()
    return row(term)


@router.put("/projects/{pid}/glossary/{gid}")
def edit_term(pid: str, gid: str, body: GlossaryInput, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    term = db.get(Glossary, gid)
    if not term or term.project_id != pid:
        raise HTTPException(404, "Terme introuvable.")
    for key, value in body.model_dump().items():
        setattr(term, key, value)
    invalidate_after_decision(db, project, term.source)
    db.add(
        Outbox(
            project_id=pid,
            event_key=f"glossary-{gid}-{project.memory_revision}",
            session_name="decisions",
            payload={"type": "HUMAN_TRANSLATION_DECISION", **body.model_dump()},
        )
    )
    db.commit()
    return row(term)


@router.delete("/projects/{pid}/glossary/{gid}")
def delete_term(pid: str, gid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    term = db.get(Glossary, gid)
    if not term or term.project_id != pid:
        raise HTTPException(404, "Terme introuvable.")
    invalidate_after_decision(db, project, term.source)
    db.delete(term)
    db.commit()
    return {"ok": True}


@router.get("/projects/{pid}/glossary/export/{format}")
def export_terms(pid: str, format: Literal["json", "csv", "tbx"], user: CurrentUser, db: DB):
    project = access(db, pid, user)
    values = [
        row(g, ("id", "project_id", "created_at"))
        for g in db.scalars(select(Glossary).where(Glossary.project_id == pid).order_by(Glossary.source))
    ]
    content, media_type = {
        "json": lambda: (export_json(values), "application/json"),
        "csv": lambda: (export_csv(values), "text/csv"),
        "tbx": lambda: (
            export_tbx(values, project.source_language, project.target_language),
            "application/x-tbx+xml",
        ),
    }[format]()
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="glossary.{format}"'},
    )


@router.post("/projects/{pid}/glossary/import")
async def import_terms(pid: str, file: UploadFile, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    data = await file.read(2 * 1024**2 + 1)
    if len(data) > 2 * 1024**2:
        raise HTTPException(413, "Glossaire trop volumineux.")
    terms = read_glossary(data, file.filename or "", project.source_language, project.target_language)
    count = 0
    for value in terms:
        existing = db.scalar(
            select(Glossary).where(Glossary.project_id == pid, Glossary.source == value.source)
        )
        if existing:
            continue  # Import never silently overwrites a human or locked term.
        db.add(Glossary(project_id=pid, **value.model_dump()))
        db.flush()
        count += 1
    invalidate_after_decision(db, project)
    db.commit()
    return {"imported": count, "skipped": len(terms) - count}


class MemorySettings(BaseModel):
    base_url: str = ""
    api_key: str | None = None
    root_uri: str = "viking://resources/epub-translator"
    auth_mode: Literal["api_key", "trusted"] = "api_key"
    account: str = Field(default="", max_length=100)
    user: str = Field(default="", max_length=100)
    enable_search: bool = True
    enable_deep_search: bool = True
    min_score: float = Field(default=0.15, ge=0, le=1)
    timeout: int = Field(default=20, ge=2, le=120)
    context_budget: int = Field(default=12000, ge=2000, le=100000)
    retrieval_budget: int = Field(default=6000, ge=500, le=50000)


@router.get("/settings/memory")
def memory_settings(_admin: Admin):
    config = memory_config()
    return {k: v for k, v in config.items() if k != "api_key"} | {"has_api_key": bool(config["api_key"])}


@router.put("/settings/memory")
def save_memory_settings(body: MemorySettings, _admin: Admin, db: DB):
    if body.base_url:
        ProviderInput.url(body.base_url)
    validate_root(body.root_uri)
    if body.auth_mode == "trusted" and (not body.account or not body.user):
        raise ValueError("Account et user requis en mode trusted.")
    saved = db.get(AppSetting, "openviking")
    data = body.model_dump(exclude={"api_key"})
    if body.api_key is not None:
        data["encrypted_key"] = encrypt(body.api_key)
    elif saved and "encrypted_key" in saved.value:
        data["encrypted_key"] = saved.value["encrypted_key"]
    if saved:
        saved.value = data
    else:
        db.add(AppSetting(key="openviking", value=data))
    db.commit()
    return {"ok": True}


@router.post("/settings/memory/test")
async def test_memory(_admin: Admin):
    config = memory_config()
    result = {"api": "not_tested", "authentication": "not_tested", "search": "not_tested", "error": ""}
    if not config["base_url"]:
        return dict(result, error="Renseignez l’URL OpenViking.")
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(config["base_url"].rstrip("/") + "/health")
            response.raise_for_status()
            result["api"] = "process_reachable"
        async with OpenVikingClient(config) as client:
            await client.request("GET", "/api/v1/fs/ls", params={"uri": "viking://resources/"})
            result["authentication"] = "ok"
            try:
                await client.find("connection test", validate_root(config["root_uri"]), limit=1)
                result["search"] = "ok"
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    result["search"] = "namespace_not_created_yet"
                else:
                    raise
    except Exception as exc:
        result["error"] = f"Échec du test : {type(exc).__name__}. Vérifiez URL, clé et version du serveur."
    return result


@router.get("/projects/{pid}/memory/status")
def memory_status(pid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user)
    counts = dict(
        db.execute(
            select(Outbox.status, func.count()).where(Outbox.project_id == pid).group_by(Outbox.status)
        ).all()
    )
    errors = list(
        db.scalars(
            select(Outbox)
            .where(Outbox.project_id == pid, Outbox.error != "")
            .order_by(Outbox.created_at.desc())
            .limit(5)
        )
    )
    catalog = db.scalar(select(Outbox).where(Outbox.event_key == f"catalog:{pid}"))
    filenames = (
        sorted(catalog.payload.get("files", {}))
        if catalog
        else ["book.md", "book-bible.json", "characters.json", "relationships.json"]
    )
    return {
        "backend": project.context_backend,
        "configured": bool(memory_config()["base_url"]),
        "outbox": counts,
        "errors": [e.error for e in errors],
        "index_freshness": "unknown",
        "root_uri": project_uri(project),
        "catalog_status": catalog.status if catalog else "not_prepared",
        "documents": [
            {
                "name": filename,
                "uri": catalog_uri(project, filename),
                "read_url": f"/api/projects/{pid}/memory/documents/{filename}",
            }
            for filename in filenames
        ],
    }


@router.post("/projects/{pid}/memory/synchronize")
def synchronize_catalog(pid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user, write=True)
    catalog = queue_catalog(db, project, force=True)
    db.commit()
    return {
        "queued": bool(catalog),
        "message": "Synchronisation du catalogue et du graphe planifiée."
        if catalog
        else "Ce projet utilise uniquement la mémoire interne.",
    }


@router.get("/projects/{pid}/memory/documents/{filename}")
async def read_memory_document(pid: str, filename: str, user: CurrentUser, db: DB):
    project = access(db, pid, user)
    if not CATALOG_NAME.fullmatch(filename):
        raise HTTPException(404, "Document inconnu.")
    try:
        async with OpenVikingClient(memory_config()) as client:
            content = await client.read(catalog_uri(project, filename))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502,
            f"Document OpenViking indisponible (HTTP {exc.response.status_code}). Lancez une synchronisation et vérifiez son état.",
        ) from None
    return Response(
        content, media_type="application/json" if filename.endswith(".json") else "text/plain; charset=utf-8"
    )


@router.post("/projects/{pid}/memory/check")
async def check_memory_catalog(pid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user)
    catalog = db.scalar(select(Outbox).where(Outbox.event_key == f"catalog:{pid}"))
    if not catalog:
        return {"catalog": "not_prepared", "message": "Lancez Synchroniser pour créer les documents nommés."}
    results = []
    async with OpenVikingClient(memory_config()) as client:
        for filename in ("book.md", "book-bible.json", "characters.json", "relationships.json"):
            uri = catalog_uri(project, filename)
            try:
                content = await client.read(uri)
                results.append(
                    {
                        "file": filename,
                        "readable": True,
                        "matches_snapshot": content == catalog.payload["files"].get(filename),
                    }
                )
            except httpx.HTTPStatusError:
                results.append({"file": filename, "readable": False, "matches_snapshot": False})
        hits = await client.find(project.title, project_uri(project), limit=12)
    expected_uris = {catalog_uri(project, name) for name in catalog.payload["files"]}
    visible = [h["uri"] for h in hits if h.get("uri") in expected_uris]
    return {
        "documents": results,
        "catalog_found_in_index": bool(visible),
        "indexed_uris": visible,
        "note": "Ce test confirme les documents retrouvés ; il ne prouve pas l’indexation de chaque événement.",
    }


@router.post("/projects/{pid}/memory/reindex")
async def reindex_memory(pid: str, user: CurrentUser, db: DB):
    project = access(db, pid, user, owner=True)
    try:
        async with OpenVikingClient(memory_config()) as client:
            result = await client.reindex(project_uri(project))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502,
            f"OpenViking refuse la réindexation (HTTP {exc.response.status_code}). "
            "Vérifiez les droits de la clé sur cette racine. La reconstruction par réécriture "
            "depuis SQL reste disponible.",
        ) from None
    return {"accepted": True, "result": result}


@router.post("/projects/{pid}/memory/rebuild")
def rebuild_memory(pid: str, user: CurrentUser, db: DB):
    access(db, pid, user, owner=True)
    entries = list(db.scalars(select(Outbox).where(Outbox.project_id == pid)))
    for event in entries:
        event.status, event.next_attempt, event.error = "pending", 0, ""
    db.commit()
    return {
        "queued": len(entries),
        "message": "Réécriture idempotente depuis SQL ; l’indexation est asynchrone.",
    }


@router.get("/prompts")
def prompts(_admin: Admin, db: DB):
    result = []
    for path in sorted(settings().prompt_dir.glob("*.txt")):
        override = db.scalar(select(Prompt).where(Prompt.name == path.stem).order_by(Prompt.version.desc()))
        result.append(
            {
                "name": path.stem,
                "version": override.version if override else 0,
                "content": override.content if override else path.read_text(),
            }
        )
    return result


class PromptInput(BaseModel):
    content: str = Field(min_length=20, max_length=20000)


@router.put("/prompts/{name}")
def update_prompt(name: str, body: PromptInput, _admin: Admin, db: DB):
    allowed = {p.stem for p in settings().prompt_dir.glob("*.txt")}
    if name not in allowed:
        raise HTTPException(404, "Prompt inconnu.")
    last = db.scalar(select(func.max(Prompt.version)).where(Prompt.name == name)) or 0
    prompt = Prompt(name=name, version=last + 1, content=body.content)
    db.add(prompt)
    db.commit()
    return row(prompt)
