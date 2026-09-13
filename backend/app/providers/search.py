from app.config import settings
from app.db import SessionLocal
from app.models import AppSetting


def search_config() -> dict:
    with SessionLocal() as db:
        saved = db.get(AppSetting, "searxng")
        if saved:
            return dict(saved.value)
    return {"base_url": settings().searxng_url, "enabled": bool(settings().searxng_url)}
