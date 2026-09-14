from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.models import AppSetting
from app.security import DB, Admin

router = APIRouter(prefix="/api/settings/recovery")


class RecoverySettings(BaseModel):
    retry_seconds: int = Field(default=60, ge=5, le=3600)


@router.get("")
def get_recovery(_admin: Admin, db: DB):
    saved = db.get(AppSetting, "provider_recovery")
    return RecoverySettings(**saved.value) if saved else RecoverySettings()


@router.put("")
def set_recovery(body: RecoverySettings, _admin: Admin, db: DB):
    saved = db.get(AppSetting, "provider_recovery")
    if saved:
        saved.value = body.model_dump()
    else:
        db.add(AppSetting(key="provider_recovery", value=body.model_dump()))
    db.commit()
    return body
