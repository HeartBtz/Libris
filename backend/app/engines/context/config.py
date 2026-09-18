import logging

from app.config import settings
from app.db import SessionLocal
from app.models import AppSetting
from app.security import SecretUnreadable, decrypt

logger = logging.getLogger("epub.memory")


def memory_config() -> dict:
    config = settings()
    result = {
        "base_url": config.openviking_url,
        "api_key": config.openviking_api_key,
        "root_uri": config.openviking_root_uri,
        "enable_search": True,
        "enable_deep_search": True,
        "context_budget": 12000,
        "retrieval_budget": 6000,
        "min_score": 0.15,
        "timeout": 20,
        "auth_mode": "api_key",
        "account": "",
        "user": "",
    }
    with SessionLocal() as db:
        saved = db.get(AppSetting, "openviking")
        if saved:
            result.update({k: v for k, v in saved.value.items() if k != "encrypted_key"})
            if "encrypted_key" in saved.value:
                try:
                    result["api_key"] = decrypt(saved.value["encrypted_key"])
                except SecretUnreadable:
                    # Translation must not stop because the optional memory service lost its key.
                    logger.warning("openviking=disabled reason=stored_key_unreadable_secret_key_changed")
                    result.update(api_key="", enable_search=False, enable_deep_search=False)
    return result
