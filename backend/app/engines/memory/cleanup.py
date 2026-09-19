"""Opt-in removal of the OpenViking documents of deleted volumes and series.

Off by default: `OPENVIKING_CLEANUP_ON_DELETE`, overridden by the `AppSetting` "openviking_cleanup"
saved from Settings › Memory · OpenViking. When it is on, deleting a volume or a series queues an
`OpenVikingCleanup` row in the same transaction as the SQL deletion, and the worker removes the
directories later (durable, retried with a growing delay, resumed after a restart, never blocking the
deletion itself). An administrator can also look for orphans left by earlier deletions (a dry run
that lists them) and queue their removal.

Safety: only whole item directories are ever removed (`cleanup_scope`: a series, a volume in its
series or standalone space, a volume of the 0.5 layout), under the configured root, and each one only
if SQL says, at the moment of the removal, that nothing lives there any more (`orphan_reason`).
"""

import asyncio
import logging
import time

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.automation_settings import saved
from app.config import settings
from app.db import SessionLocal
from app.engines.context.config import memory_config
from app.models import AppSetting, OpenVikingCleanup, Outbox, Project, Series
from app.providers.openviking import (
    LIBRIS_ID,
    OpenVikingClient,
    cleanup_scope,
    project_uri,
    series_uri,
    validate_root,
)

logger = logging.getLogger("epub.memory")

CLEANUP_KEY = "openviking_cleanup"
# A write already sent by another worker process lands before the removal, not after it.
GRACE_SECONDS = 30
LEASE_SECONDS = 300
MAX_BACKOFF = 3600
DOCUMENTS_LOGGED = 100
ORPHANS_LISTED = 500


# Setting -------------------------------------------------------------------------------------------


def cleanup_enabled(db: Session | None = None) -> bool:
    value = saved(db, CLEANUP_KEY).get("enabled")
    return value if isinstance(value, bool) else settings().openviking_cleanup_on_delete


def cleanup_view(db: Session) -> dict:
    return {
        "enabled": cleanup_enabled(db),
        "default": settings().openviking_cleanup_on_delete,
        "saved": isinstance(saved(db, CLEANUP_KEY).get("enabled"), bool),
        "configured": bool(memory_config()["base_url"]),
    }


def save_cleanup_setting(db: Session, enabled: bool | None) -> None:
    """None forgets the saved value: OPENVIKING_CLEANUP_ON_DELETE applies again."""
    row = db.get(AppSetting, CLEANUP_KEY)
    if enabled is None:
        if row:
            db.delete(row)
    elif row:
        row.value = {"enabled": enabled}
    else:
        db.add(AppSetting(key=CLEANUP_KEY, value={"enabled": enabled}))


# What SQL says about a directory -------------------------------------------------------------------


def orphan_reason(db: Session, uri: str, root: str) -> str | None:
    """Why this item directory holds nothing SQL still owns, or None when it must stay."""
    scope = cleanup_scope(uri, root)
    if scope is None:
        return None
    kind, ids = scope
    if kind == "series":
        owner_id, series_id = ids
        series = db.get(Series, series_id)
        return None if series and series.owner_id == owner_id else "series_deleted"
    owner_id, project_id = ids[0], ids[-1]
    project = db.get(Project, project_id)
    if project is None:
        return "volume_deleted"
    if kind == "legacy":
        # Written by Libris 0.5; since 0.6 the volume lives in its series or standalone space.
        return "legacy_layout"
    expected = ids[1] if kind == "series_volume" else None
    if project.owner_id == owner_id and project.series_id == expected:
        return None
    return "volume_moved"


# Queueing ------------------------------------------------------------------------------------------


def _active_root() -> str | None:
    config = memory_config()
    if not config["base_url"]:
        return None
    try:
        return validate_root(config["root_uri"])
    except ValueError:
        return None


def volume_prefixes(db: Session, project: Project, root: str) -> list[str]:
    """Every place this volume may have been written under the root: its current space, its 0.5 space,
    and earlier series or standalone spaces seen in the send queue (a volume moved between series)."""
    prefixes = [project_uri(project), f"{root}/{project.owner_id}/{project.id}"]
    for uri in db.scalars(select(Outbox.uri).where(Outbox.project_id == project.id, Outbox.uri.is_not(None))):
        head = uri.split(f"/{project.id}/", 1)[0] + f"/{project.id}"
        if cleanup_scope(head, root):
            prefixes.append(head)
    return [p for p in dict.fromkeys(prefixes) if cleanup_scope(p, root)]


def queue_volume_cleanup(db: Session, project: Project, user_id: str) -> OpenVikingCleanup | None:
    """Called before the project row is deleted, in the same transaction."""
    root = _active_root()
    if root is None or not cleanup_enabled(db):
        return None
    row = OpenVikingCleanup(
        kind="volume",
        owner_id=project.owner_id,
        target_id=project.id,
        label=(project.title or "")[:500],
        requested_by=user_id,
        root_uri=root,
        uris=volume_prefixes(db, project, root),
        results=[],
        next_attempt=time.time() + GRACE_SECONDS,
    )
    db.add(row)
    return row


def queue_series_cleanup(db: Session, series: Series, user_id: str) -> OpenVikingCleanup | None:
    root = _active_root()
    if root is None or not cleanup_enabled(db):
        return None
    row = OpenVikingCleanup(
        kind="series",
        owner_id=series.owner_id,
        target_id=series.id,
        label=(series.name or "")[:500],
        requested_by=user_id,
        root_uri=root,
        uris=[series_uri(series.owner_id, series.id)],
        results=[],
        next_attempt=time.time() + GRACE_SECONDS,
    )
    db.add(row)
    return row


def queue_orphan_cleanup(
    db: Session, uris: list[str], user_id: str
) -> tuple[OpenVikingCleanup | None, list[str]]:
    """Queues the listed directories SQL still considers orphans; returns the row and the refused URIs."""
    root = _active_root()
    if root is None:
        return None, list(uris)
    accepted = [uri for uri in dict.fromkeys(uris) if orphan_reason(db, uri, root)]
    refused = [uri for uri in uris if uri not in accepted]
    if not accepted:
        return None, refused
    row = OpenVikingCleanup(
        kind="orphans",
        requested_by=user_id,
        label="",
        root_uri=root,
        uris=accepted,
        results=[],
        next_attempt=0,
    )
    db.add(row)
    return row, refused


def cleanup_row(row: OpenVikingCleanup) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "target_id": row.target_id,
        "owner_id": row.owner_id,
        "label": row.label,
        "status": row.status,
        "attempts": row.attempts,
        "next_attempt": row.next_attempt,
        "error": row.error,
        "created_at": row.created_at,
        "finished_at": row.finished_at,
        "uris": row.uris,
        "results": row.results,
    }


# Dry run -------------------------------------------------------------------------------------------


def _children(entries: list[dict] | None, parent: str) -> list[str]:
    """Directory names directly under `parent`; the URI is always rebuilt from the parent, never taken as is."""
    names = []
    for entry in entries or []:
        if not entry.get("isDir"):
            continue
        uri = entry["uri"].rstrip("/")
        if uri.startswith(parent + "/") and "/" not in uri[len(parent) + 1 :]:
            names.append(uri[len(parent) + 1 :])
    return names


async def scan_orphans() -> dict:
    """Lists the item directories under the root that SQL no longer owns. Removes nothing."""
    root = _active_root()
    if root is None:
        raise ValueError("OpenViking n’est pas configuré sur ce serveur.")
    candidates: list[str] = []
    async with OpenVikingClient(memory_config()) as client:
        for owner in _children(await client.ls(root), root):
            base = f"{root}/{owner}"
            if not LIBRIS_ID.fullmatch(owner):
                continue  # not a Libris owner directory: left alone
            for child in _children(await client.ls(base), base):
                if child == "series":
                    for series_id in _children(await client.ls(f"{base}/series"), f"{base}/series"):
                        space = f"{base}/series/{series_id}"
                        candidates.append(space)
                        volumes = f"{space}/volumes"
                        candidates += [
                            f"{volumes}/{pid}" for pid in _children(await client.ls(volumes), volumes)
                        ]
                elif child == "standalone":
                    candidates += [
                        f"{base}/standalone/{pid}"
                        for pid in _children(await client.ls(f"{base}/standalone"), f"{base}/standalone")
                    ]
                else:
                    candidates.append(f"{base}/{child}")
    orphans = []
    with SessionLocal() as db:
        removed_series = set()
        for uri in candidates:
            reason = orphan_reason(db, uri, root)
            if not reason:
                continue
            if cleanup_scope(uri, root)[0] == "series":
                removed_series.add(uri)
            elif any(uri.startswith(space + "/") for space in removed_series):
                continue  # already inside an orphan series directory
            orphans.append({"uri": uri, "reason": reason})
    return {"root_uri": root, "orphans": orphans[:ORPHANS_LISTED], "total": len(orphans)}


# Worker --------------------------------------------------------------------------------------------


def _claim(now: float) -> OpenVikingCleanup | None:
    with SessionLocal() as db:
        candidate = db.scalar(
            select(OpenVikingCleanup.id)
            .where(
                or_(
                    (OpenVikingCleanup.status == "pending") & (OpenVikingCleanup.next_attempt <= now),
                    (OpenVikingCleanup.status == "running") & (OpenVikingCleanup.lease_until < now),
                )
            )
            .order_by(OpenVikingCleanup.created_at)
            .limit(1)
        )
        if candidate is None:
            return None
        claimed = db.execute(
            update(OpenVikingCleanup)
            .where(
                OpenVikingCleanup.id == candidate,
                or_(
                    OpenVikingCleanup.status == "pending",
                    (OpenVikingCleanup.status == "running") & (OpenVikingCleanup.lease_until < now),
                ),
            )
            .values(status="running", lease_until=now + LEASE_SECONDS)
        ).rowcount
        db.commit()
        if not claimed:
            return None
        row = db.get(OpenVikingCleanup, candidate)
        db.expunge(row)
        return row


async def _remove(client: OpenVikingClient, uri: str, root: str) -> dict:
    with SessionLocal() as db:
        reason = orphan_reason(db, uri, root)
    if reason is None:
        return {"uri": uri, "status": "kept", "reason": "in_use"}
    entries = await client.ls(uri, recursive=True, limit=DOCUMENTS_LOGGED * 10)
    if entries is None:
        return {"uri": uri, "status": "absent", "reason": reason}
    documents = [e["uri"] for e in entries if not e["isDir"] and e["uri"].startswith(uri + "/")]
    result = await client.remove_tree(uri)
    entry = {
        "uri": uri,
        "status": "removed" if result is not None else "absent",
        "reason": reason,
        "document_count": len(documents),
        "documents": documents[:DOCUMENTS_LOGGED],
    }
    if result and isinstance(result.get("estimated_deleted_count"), int):
        entry["estimated_deleted_count"] = result["estimated_deleted_count"]
    return entry


async def run_cleanup(row: OpenVikingCleanup) -> None:
    config = memory_config()
    done = {entry["uri"] for entry in row.results}
    try:
        if validate_root(config["root_uri"]) != row.root_uri:
            raise ValueError("La racine OpenViking a changé depuis la suppression.")
        async with OpenVikingClient(config) as client:
            for uri in row.uris:
                if uri in done:
                    continue  # resumed: already handled before a restart
                entry = await _remove(client, uri, row.root_uri)
                with SessionLocal() as db:
                    current = db.get(OpenVikingCleanup, row.id)
                    current.results = [*current.results, entry]
                    current.lease_until = time.time() + LEASE_SECONDS
                    db.commit()
                if entry["status"] == "removed":
                    logger.info(
                        "openviking_cleanup=%s removed=%s documents=%s", row.id, uri, entry["document_count"]
                    )
    except asyncio.CancelledError:
        # Worker shutdown: the next start resumes after the directories already handled.
        with SessionLocal() as db:
            current = db.get(OpenVikingCleanup, row.id)
            current.status, current.lease_until = "pending", 0
            db.commit()
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is retried later with its reason
        with SessionLocal() as db:
            current = db.get(OpenVikingCleanup, row.id)
            current.attempts += 1
            current.status, current.lease_until = "pending", 0
            current.error = f"OpenViking : {type(exc).__name__}" + (
                f" ({exc})" if isinstance(exc, ValueError) else ""
            )
            current.next_attempt = time.time() + min(MAX_BACKOFF, 30 * 2 ** min(current.attempts, 7))
            db.commit()
        logger.warning("openviking_cleanup=%s status=deferred reason=%s", row.id, type(exc).__name__)
        return
    with SessionLocal() as db:
        current = db.get(OpenVikingCleanup, row.id)
        current.status, current.error, current.finished_at, current.lease_until = "done", "", time.time(), 0
        current.attempts += 1
        db.commit()


async def run_cleanups(limit: int = 5) -> int:
    """One worker turn: handles up to `limit` due cleanups. Nothing happens while OpenViking is unset."""
    if not memory_config()["base_url"]:
        return 0
    handled = 0
    while handled < limit:
        row = await asyncio.to_thread(_claim, time.time())
        if row is None:
            break
        await run_cleanup(row)
        handled += 1
    return handled
