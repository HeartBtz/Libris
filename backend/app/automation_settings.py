"""Global autopilot, webhook and recovery settings an administrator may change at runtime.

The environment (`AUTOPILOT_*`, `API_WEBHOOK_*`, `PROVIDER_RECOVERY_BASE_SECONDS`) gives the
defaults; a value saved from the interface (`AppSetting` rows "autopilot", "webhooks" and
"provider_recovery", see app.api.automation and app.api.recovery) wins over it until it is reset. Every code path that uses one of these values reads it here, so a change
applies to the next decision without a restart. Per-volume choices (`config["autopilot"]`,
`config["fallback_provider_ids"]`) still come first where they exist.
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import AppSetting
from app.security import SecretUnreadable, decrypt

logger = logging.getLogger("epub.settings")

AUTOPILOT_KEY = "autopilot"
WEBHOOKS_KEY = "webhooks"
RECOVERY_KEY = "provider_recovery"
AUTOPILOT_FIELDS = (
    "enabled",
    "max_rounds",
    "fallback_providers",
    "outage_max_retries",
    "outage_max_wait_seconds",
    "glossary_min_confidence",
    "identity_min_confidence",
    "bible_min_coverage",
    "stale_min_coverage",
)
WEBHOOK_FIELDS = ("hosts", "private_networks", "max_attempts", "timeout_seconds")


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def autopilot_defaults() -> dict:
    config = settings()
    return {
        "enabled": config.autopilot_enabled,
        "max_rounds": config.autopilot_max_rounds,
        # Names or ids, as AUTOPILOT_FALLBACK_PROVIDERS writes them.
        "fallback_providers": _split(config.autopilot_fallback_providers),
        "outage_max_retries": config.autopilot_outage_max_retries,
        "outage_max_wait_seconds": config.autopilot_outage_max_wait_seconds,
        "glossary_min_confidence": config.autopilot_glossary_min_confidence,
        "identity_min_confidence": config.autopilot_identity_min_confidence,
        "bible_min_coverage": config.autopilot_bible_min_coverage,
        "stale_min_coverage": config.autopilot_stale_min_coverage,
    }


def webhook_defaults() -> dict:
    config = settings()
    return {
        "hosts": [item.casefold().rstrip(".") for item in _split(config.api_webhook_hosts)],
        "private_networks": _split(config.api_webhook_private_networks),
        "max_attempts": config.api_webhook_max_attempts,
        "timeout_seconds": config.api_webhook_timeout_seconds,
    }


def saved(db: Session | None, key: str) -> dict:
    """The saved override (possibly partial), or an empty dict."""
    if db is None:
        with SessionLocal() as own:
            return saved(own, key)
    row = db.get(AppSetting, key)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


def autopilot_config(db: Session | None = None) -> dict:
    stored = saved(db, AUTOPILOT_KEY)
    return {**autopilot_defaults(), **{k: v for k, v in stored.items() if k in AUTOPILOT_FIELDS}}


def webhook_config(db: Session | None = None) -> dict:
    stored = saved(db, WEBHOOKS_KEY)
    return {**webhook_defaults(), **{k: v for k, v in stored.items() if k in WEBHOOK_FIELDS}}


def webhook_secret(db: Session | None = None) -> str:
    """The global HMAC secret: the one saved from the interface, else API_WEBHOOK_SECRET."""
    encrypted = saved(db, WEBHOOKS_KEY).get("encrypted_secret")
    if encrypted:
        try:
            return decrypt(encrypted)
        except SecretUnreadable:
            logger.warning("webhooks=saved_secret_unreadable_secret_key_changed")
    return settings().api_webhook_secret


def recovery_base_seconds(db: Session | None = None) -> int:
    """First wait before retrying an unavailable provider: the saved delay, else the environment."""
    value = saved(db, RECOVERY_KEY).get("retry_seconds")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return settings().provider_recovery_base_seconds


# Cost budgets (app.engines.budget): BUDGET_* gives the defaults, the "budget" row saved from
# Settings › Budgets wins over it until it is reset (app.api.budget).
BUDGET_KEY = "budget"
BUDGET_FIELDS = ("default_book", "switch_threshold", "on_estimate")


def budget_defaults() -> dict:
    config = settings()
    return {
        "default_book": config.budget_default_book,
        "switch_threshold": config.budget_switch_threshold,
        "on_estimate": config.budget_on_estimate,
    }


def budget_config(db: Session | None = None) -> dict:
    stored = saved(db, BUDGET_KEY)
    return {**budget_defaults(), **{k: v for k, v in stored.items() if k in BUDGET_FIELDS}}
