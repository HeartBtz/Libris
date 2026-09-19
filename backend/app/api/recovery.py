"""Administration of the automatic recovery delay (see app.automation_settings).

GET answers the delay in force, the environment value (`PROVIDER_RECOVERY_BASE_SECONDS`) and
whether a saved value overrides it; PUT saves a delay; DELETE forgets it (the environment applies
again).
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.automation_settings import RECOVERY_KEY, recovery_base_seconds, saved
from app.config import settings
from app.models import AppSetting
from app.security import DB, Admin

router = APIRouter(prefix="/api/settings/recovery")


class RecoverySettings(BaseModel):
    retry_seconds: int = Field(ge=5, le=3600)


def recovery_view(db: Session) -> dict:
    return {
        "retry_seconds": recovery_base_seconds(db),
        "default_seconds": settings().provider_recovery_base_seconds,
        "saved": "retry_seconds" in saved(db, RECOVERY_KEY),
    }


@router.get("")
def get_recovery(_admin: Admin, db: DB):
    return recovery_view(db)


@router.put("")
def set_recovery(body: RecoverySettings, _admin: Admin, db: DB):
    row = db.get(AppSetting, RECOVERY_KEY)
    if row:
        row.value = body.model_dump()
    else:
        db.add(AppSetting(key=RECOVERY_KEY, value=body.model_dump()))
    db.commit()
    return recovery_view(db)


@router.delete("")
def reset_recovery(_admin: Admin, db: DB):
    """Forgets the saved delay: PROVIDER_RECOVERY_BASE_SECONDS applies again."""
    row = db.get(AppSetting, RECOVERY_KEY)
    if row:
        db.delete(row)
        db.commit()
    return recovery_view(db)
