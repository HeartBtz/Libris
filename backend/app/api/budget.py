"""Cost budgets (app.engines.budget): the installation's defaults, a book's cap, an API token's cap.

`/api/settings/budget` (administrators): GET answers the values in force, the environment defaults and
whether a saved value overrides them; PUT saves a complete set; DELETE forgets it.
`/api/projects/{id}/budget`: GET the book's cap, what it has cost and its last job's estimate against
its real cost; PUT sets its own cap (owner only). `/api/tokens/{id}/budget`: PUT sets a token's cap.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.tokens import token_view
from app.automation_settings import BUDGET_KEY, budget_config, budget_defaults, saved
from app.engines.budget import book_view
from app.engines.series.audit import audit
from app.models import ApiToken, AppSetting
from app.security import DB, Admin, CurrentUser, access

router = APIRouter(prefix="/api")
MAX_AMOUNT = 1_000_000_000


class BudgetSettings(BaseModel):
    # Default cap of a book without its own (0: none), in the currency of the provider prices.
    default_book: float = Field(default=0, ge=0, le=MAX_AMOUNT)
    switch_threshold: float = Field(default=0.9, ge=0.5, le=1)
    on_estimate: Literal["warn", "refuse"] = "warn"


class BookBudget(BaseModel):
    # None: the installation's default; 0: no cap for this book, even with a default.
    amount: float | None = Field(default=None, ge=0, le=MAX_AMOUNT)


class TokenBudget(BaseModel):
    # None: no cap.
    amount: float | None = Field(default=None, gt=0, le=MAX_AMOUNT)
    period: Literal["month", "total"] = "month"


def settings_view(db: Session) -> dict:
    return {"values": budget_config(db), "defaults": budget_defaults(), "saved": bool(saved(db, BUDGET_KEY))}


@router.get("/settings/budget")
def get_budget_settings(_admin: Admin, db: DB):
    return settings_view(db)


@router.put("/settings/budget")
def set_budget_settings(body: BudgetSettings, _admin: Admin, db: DB):
    row = db.get(AppSetting, BUDGET_KEY)
    if row:
        row.value = body.model_dump()
    else:
        db.add(AppSetting(key=BUDGET_KEY, value=body.model_dump()))
    db.commit()
    return settings_view(db)


@router.delete("/settings/budget")
def reset_budget_settings(_admin: Admin, db: DB):
    row = db.get(AppSetting, BUDGET_KEY)
    if row:
        db.delete(row)
        db.commit()
    return settings_view(db)


@router.get("/projects/{project_id}/budget")
def get_book_budget(project_id: str, user: CurrentUser, db: DB):
    return book_view(db, access(db, project_id, user))


@router.put("/projects/{project_id}/budget")
def set_book_budget(project_id: str, body: BookBudget, user: CurrentUser, db: DB):
    project = access(db, project_id, user, owner=True)
    config = {key: value for key, value in (project.config or {}).items() if key != "budget_amount"}
    if body.amount is not None:
        config["budget_amount"] = body.amount
    project.config = config
    db.commit()
    return book_view(db, project)


@router.put("/tokens/{token_id}/budget")
def set_token_budget(token_id: str, body: TokenBudget, user: CurrentUser, db: DB):
    token = db.get(ApiToken, token_id)
    if not token or token.owner_id != user.id:
        raise HTTPException(404, "Jeton introuvable.")
    token.budget_amount, token.budget_period = body.amount, body.period
    audit(db, owner_id=user.id, actor_id=user.id, action="api_token_budget", token_id=token.id,
          amount=body.amount, period=body.period)  # fmt: skip
    db.commit()
    return token_view(token, db)
