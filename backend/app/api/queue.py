"""The fair queue as people see it, and its administration (see app.jobs.fairness).

GET /api/queue lists the running and waiting jobs the caller may see (an administrator sees every
account's) with each waiting job's place and why it waits. A person may lower or raise the priority
of a job of a book they can edit, within their ceiling. The quotas and the aging delay are runtime
settings: GET answers the values in force and the environment defaults, PUT saves them, DELETE goes
back to the environment.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.automation_settings import QUEUE_KEY, queue_config, queue_defaults, saved
from app.jobs import clock
from app.jobs.fairness import (
    PRIORITIES,
    QueueRefused,
    account_limits,
    allowed_priority,
    label,
    requested_priority,
    running_count,
    snapshot,
    waiting_count,
)
from app.jobs.queue import HELD, emit
from app.models import AppSetting, Job, Membership, Project, Provider, User
from app.schemas import StrictModel
from app.security import DB, Admin, CurrentUser, access

router = APIRouter(prefix="/api")

Priority = Literal["low", "normal", "high"]


def visible_projects(db: Session, user: User) -> set[str] | None:
    """None: every project (administrators); otherwise the books the person owns or shares."""
    if user.admin:
        return None
    shared = select(Membership.project_id).where(Membership.user_id == user.id)
    return set(db.scalars(select(Project.id).where(or_(Project.owner_id == user.id, Project.id.in_(shared)))))


@router.get("/queue")
def queue(user: CurrentUser, db: DB, project_id: str | None = None):
    visible = visible_projects(db, user)
    if project_id is not None:
        access(db, project_id, user)
        visible = {project_id}
    now = clock.now(db)
    found = snapshot(db, now, visible)
    entries = [*found["running"], *found["waiting"]]
    projects = {
        p.id: p for p in db.scalars(select(Project).where(Project.id.in_({e["project_id"] for e in entries})))
    }
    owners = {
        u.id: u.username
        for u in db.scalars(select(User).where(User.id.in_({e["owner_id"] for e in entries})))
    }
    providers = dict(
        db.execute(
            select(Provider.id, Provider.name).where(
                Provider.id.in_({e["provider_id"] for e in entries} - {None})
            )
        ).all()
    )

    def view(entry: dict) -> dict:
        project = projects.get(entry["project_id"])
        return {
            **{
                key: value
                for key, value in entry.items()
                if key != "token_id" and (user.admin or key != "owner_id")
            },
            "title": project.title if project else "",
            "owner": owners.get(entry["owner_id"], "") if user.admin or entry["owner_id"] == user.id else "",
            "mine": entry["owner_id"] == user.id,
            "provider": providers.get(entry["provider_id"], ""),
        }

    config = queue_config(db)
    limits = account_limits(config, user.id)
    return {
        "running": [view(entry) for entry in found["running"]],
        "waiting": [view(entry) for entry in found["waiting"]],
        "totals": found["totals"],
        "account": {
            "running": running_count(db, now, owner_id=user.id),
            "waiting": waiting_count(db, owner_id=user.id),
            "max_running": limits["max_running"],
            "max_queued": limits["max_queued"],
            "max_priority": label(allowed_priority(db, user, config)),
        },
        "aging_minutes": config["aging_minutes"],
    }


class PriorityInput(StrictModel):
    priority: Priority


@router.put("/queue/{job_id}/priority")
def set_priority(job_id: str, body: PriorityInput, user: CurrentUser, db: DB):
    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise HTTPException(404, "Travail introuvable.")
    if not user.admin:  # administrators arrange the whole queue
        access(db, job.project_id, user, write=True)
    if job.status not in HELD:
        raise HTTPException(409, "Ce travail est déjà terminé.")
    try:
        job.priority = requested_priority(db, user, body.priority)
    except QueueRefused as exc:
        raise HTTPException(exc.status, exc.detail) from None
    emit(db, job.project_id, job_id=job.id, status=job.status, priority=body.priority)
    db.commit()
    return {"job_id": job.id, "priority": label(job.priority)}


class AccountQuota(StrictModel):
    user_id: str = Field(min_length=1, max_length=36)
    # Null: the installation's value for this quota; 0: no limit for this account.
    max_running: int | None = Field(default=None, ge=0, le=1000)
    max_queued: int | None = Field(default=None, ge=0, le=100_000)
    max_priority: Priority | None = None


class QueueSettings(StrictModel):
    max_running_per_account: int = Field(default=0, ge=0, le=1000)
    max_queued_per_account: int = Field(default=0, ge=0, le=100_000)
    aging_minutes: int = Field(default=60, ge=0, le=7 * 24 * 60)
    accounts: list[AccountQuota] = Field(default_factory=list, max_length=1000)


def settings_view(db: Session) -> dict:
    values = queue_config(db)
    stored = saved(db, QUEUE_KEY)
    names = dict(db.execute(select(User.id, User.username).where(User.id.in_(set(values["accounts"])))).all())
    fields = ("max_running", "max_queued", "max_priority")
    accounts = [
        {"user_id": user_id, "username": names[user_id], **{key: own.get(key) for key in fields}}
        for user_id, own in values["accounts"].items()
        if user_id in names
    ]
    return {
        "values": {key: values[key] for key in queue_defaults()},
        "defaults": queue_defaults(),
        "accounts": sorted(accounts, key=lambda item: item["username"].casefold()),
        "saved": bool(stored),
        "priorities": list(PRIORITIES),
    }


@router.get("/settings/queue")
def get_queue_settings(_admin: Admin, db: DB):
    return settings_view(db)


@router.put("/settings/queue")
def set_queue_settings(body: QueueSettings, _admin: Admin, db: DB):
    accounts = {}
    for item in body.accounts:
        if db.get(User, item.user_id) is None:
            raise HTTPException(404, "Compte introuvable.")
        own = {k: v for k, v in item.model_dump(exclude={"user_id"}).items() if v is not None}
        if own:
            accounts[item.user_id] = own
    value = {**body.model_dump(exclude={"accounts"}), "accounts": accounts}
    row = db.get(AppSetting, QUEUE_KEY)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=QUEUE_KEY, value=value))
    db.commit()
    return settings_view(db)


@router.delete("/settings/queue")
def reset_queue_settings(_admin: Admin, db: DB):
    row = db.get(AppSetting, QUEUE_KEY)
    if row:
        db.delete(row)
        db.commit()
    return settings_view(db)
