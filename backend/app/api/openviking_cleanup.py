"""Administration of the OpenViking cleanup (see app.engines.memory.cleanup).

`/api/settings/memory/cleanup`: GET the switch in force, PUT saves it, DELETE forgets it
(OPENVIKING_CLEANUP_ON_DELETE applies again). `/cleanups`: the log of queued and finished removals,
and a retry. `/orphans/scan` is the dry run (removes nothing); `/orphans/clean` queues the removal
of directories the dry run listed, each checked again against the database.
"""

import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.engines.memory.cleanup import (
    cleanup_row,
    cleanup_view,
    queue_orphan_cleanup,
    save_cleanup_setting,
    scan_orphans,
)
from app.models import OpenVikingCleanup
from app.security import DB, Admin

router = APIRouter(prefix="/api/settings/memory")


class CleanupSetting(BaseModel):
    enabled: bool


class OrphanSelection(BaseModel):
    uris: list[str] = Field(min_length=1, max_length=500)


@router.get("/cleanup")
def get_cleanup(_admin: Admin, db: DB):
    return cleanup_view(db)


@router.put("/cleanup")
def set_cleanup(body: CleanupSetting, _admin: Admin, db: DB):
    save_cleanup_setting(db, body.enabled)
    db.commit()
    return cleanup_view(db)


@router.delete("/cleanup")
def reset_cleanup(_admin: Admin, db: DB):
    save_cleanup_setting(db, None)
    db.commit()
    return cleanup_view(db)


@router.get("/cleanups")
def list_cleanups(_admin: Admin, db: DB, limit: int = 50):
    rows = db.scalars(
        select(OpenVikingCleanup).order_by(OpenVikingCleanup.created_at.desc()).limit(max(1, min(limit, 200)))
    )
    return [cleanup_row(row) for row in rows]


@router.post("/cleanups/{cleanup_id}/retry")
def retry_cleanup(cleanup_id: str, _admin: Admin, db: DB):
    row = db.get(OpenVikingCleanup, cleanup_id)
    if row is None:
        raise HTTPException(404, "Nettoyage introuvable.")
    if row.status == "pending":
        row.next_attempt = 0
        db.commit()
    return cleanup_row(row)


@router.post("/orphans/scan")
async def scan(_admin: Admin):
    """Dry run: lists what a cleanup would remove, without removing anything."""
    try:
        return await scan_orphans()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except httpx.HTTPError as exc:
        status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        reason = f"HTTP {status}" if status else type(exc).__name__
        raise HTTPException(
            502, f"OpenViking n’a pas pu lister ses documents ({reason}). Rien n’a été supprimé."
        ) from None


@router.post("/orphans/clean")
def clean(body: OrphanSelection, admin: Admin, db: DB):
    row, refused = queue_orphan_cleanup(db, body.uris, admin.id)
    if row is None:
        raise HTTPException(409, "Aucun de ces dossiers n’est orphelin : rien n’est supprimé.")
    db.commit()
    return {"cleanup": cleanup_row(row), "refused": refused, "queued_at": time.time()}
