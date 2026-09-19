"""Administration of the global autopilot and webhook settings (see app.automation_settings).

GET answers the values in force, the environment defaults and whether a saved value overrides
them; PUT saves a complete set; DELETE forgets the saved values (the environment applies again).
The webhook signing secret is write-only: answers only say whether one is configured and where.
"""

import ipaddress
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.automation_settings import (
    AUTOPILOT_KEY,
    WEBHOOKS_KEY,
    autopilot_config,
    autopilot_defaults,
    saved,
    webhook_config,
    webhook_defaults,
)
from app.config import settings
from app.models import AppSetting, Provider
from app.security import DB, Admin, encrypt

router = APIRouter(prefix="/api/settings")

HOST = re.compile(r"^(\*\.)?(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*$")
MAX_ITEMS = 100


class AutopilotSettings(BaseModel):
    enabled: bool = True
    max_rounds: int = Field(default=3, ge=1, le=10)
    # Provider ids (or names, as the environment variable allows), tried in this order.
    fallback_providers: list[str] = Field(default_factory=list, max_length=20)
    outage_max_retries: int = Field(default=5, ge=1, le=100)
    outage_max_wait_seconds: int = Field(default=3600, ge=0, le=7 * 86400)
    glossary_min_confidence: float = Field(default=0.75, ge=0, le=1)
    identity_min_confidence: float = Field(default=0.8, ge=0, le=1)
    bible_min_coverage: float = Field(default=0.8, ge=0, le=1)
    stale_min_coverage: float = Field(default=0.5, ge=0, le=1)


class WebhookSettings(BaseModel):
    hosts: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    private_networks: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    max_attempts: int = Field(default=6, ge=1, le=20)
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    # Write-only. Absent or null: the saved secret is kept; `clear_secret` forgets it.
    secret: str | None = Field(default=None, max_length=512)
    clear_secret: bool = False


def _store(db: Session, key: str, value: dict) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def _forget(db: Session, key: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        db.delete(row)
        db.commit()


def _resolve(db: Session, tokens: list[str]) -> list[str]:
    """Ids of the providers named by `tokens` (ids or names) that still exist, in order."""
    found = []
    for token in tokens:
        provider = db.get(Provider, token) or db.scalar(
            select(Provider).where(Provider.name == token).limit(1)
        )
        if provider:
            found.append(provider.id)
    return list(dict.fromkeys(found))


def autopilot_view(db: Session) -> dict:
    values = autopilot_config(db)
    return {
        "values": values,
        "defaults": autopilot_defaults(),
        "saved": bool(saved(db, AUTOPILOT_KEY)),
        # The providers of the chain as they exist now (a deleted one is skipped when the chain is built).
        "fallback_provider_ids": _resolve(db, values["fallback_providers"]),
    }


@router.get("/autopilot")
def get_autopilot(_admin: Admin, db: DB):
    return autopilot_view(db)


@router.put("/autopilot")
def set_autopilot(body: AutopilotSettings, _admin: Admin, db: DB):
    tokens = list(dict.fromkeys(token.strip() for token in body.fallback_providers if token.strip()))
    ids = []
    for token in tokens:
        resolved = _resolve(db, [token])
        if not resolved:
            raise HTTPException(422, "Fournisseur de secours inconnu.")
        ids.append(resolved[0])
    _store(db, AUTOPILOT_KEY, {**body.model_dump(), "fallback_providers": list(dict.fromkeys(ids))})
    return autopilot_view(db)


@router.delete("/autopilot")
def reset_autopilot(_admin: Admin, db: DB):
    _forget(db, AUTOPILOT_KEY)
    return autopilot_view(db)


def webhook_view(db: Session) -> dict:
    stored = saved(db, WEBHOOKS_KEY)
    source = (
        "saved"
        if stored.get("encrypted_secret")
        else "environment"
        if settings().api_webhook_secret
        else "none"
    )
    return {
        "values": webhook_config(db),
        "defaults": webhook_defaults(),
        "saved": any(
            key in stored for key in ("hosts", "private_networks", "max_attempts", "timeout_seconds")
        ),
        "secret": {"configured": source != "none", "source": source},
    }


def _hosts(values: list[str]) -> list[str]:
    hosts = []
    for value in values:
        host = value.strip().casefold().rstrip(".")
        if not host:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not HOST.match(host):
                raise HTTPException(422, f"Hôte de webhook invalide : {value.strip()[:100]}") from None
        hosts.append(host)
    return list(dict.fromkeys(hosts))


def _networks(values: list[str]) -> list[str]:
    networks = []
    for value in values:
        if not value.strip():
            continue
        try:
            networks.append(str(ipaddress.ip_network(value.strip(), strict=False)))
        except ValueError:
            raise HTTPException(
                422, f"Réseau privé invalide (notation CIDR attendue) : {value.strip()[:100]}"
            ) from None
    return list(dict.fromkeys(networks))


@router.get("/webhooks")
def get_webhooks(_admin: Admin, db: DB):
    return webhook_view(db)


@router.put("/webhooks")
def set_webhooks(body: WebhookSettings, _admin: Admin, db: DB):
    value = {
        "hosts": _hosts(body.hosts),
        "private_networks": _networks(body.private_networks),
        "max_attempts": body.max_attempts,
        "timeout_seconds": body.timeout_seconds,
    }
    previous = saved(db, WEBHOOKS_KEY).get("encrypted_secret")
    if body.secret:
        if len(body.secret) < 32:
            raise HTTPException(422, "Le secret des webhooks doit contenir au moins 32 caractères.")
        value["encrypted_secret"] = encrypt(body.secret)
    elif previous and not body.clear_secret:
        value["encrypted_secret"] = previous
    _store(db, WEBHOOKS_KEY, value)
    return webhook_view(db)


@router.delete("/webhooks")
def reset_webhooks(_admin: Admin, db: DB):
    """Forgets the saved values and the saved secret: the environment applies again."""
    _forget(db, WEBHOOKS_KEY)
    return webhook_view(db)
