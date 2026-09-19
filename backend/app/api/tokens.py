"""API tokens: managed from the interface (session cookie), used by the automation API (Bearer).

A token is `lbr_<8 identifying characters>_<secret>`. Only its SHA-256 is stored; the whole value is
shown once, in the answer that creates it. The identifying part (`prefix`) finds the row, then the
hashes are compared in constant time. The cookie never opens /api/v1, and a token never opens /api/*.
"""

import hashlib
import hmac
import secrets
import string
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from app.config import settings
from app.engines.series.audit import audit
from app.models import ApiToken, User
from app.schemas import StrictModel
from app.security import DB, CurrentUser, encrypt

router = APIRouter(prefix="/api/tokens")

SCOPES = ("series:read", "content:write", "pipeline:start", "jobs:read", "jobs:control", "results:read")
Scope = Literal["series:read", "content:write", "pipeline:start", "jobs:read", "jobs:control", "results:read"]
PREFIX = "lbr_"
ALPHABET = string.ascii_lowercase + string.digits
MAX_TOKENS = 50
USAGE_PRECISION = 60  # last_used_at is written at most once a minute per token
CHALLENGE = {"WWW-Authenticate": 'Bearer realm="libris"'}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """The secret to show once, and its identifying prefix."""
    prefix = PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{prefix}_{secrets.token_urlsafe(32)}", prefix


def state(token: ApiToken, now: float | None = None) -> str:
    if token.revoked_at:
        return "revoked"
    if token.expires_at and token.expires_at <= (now or time.time()):
        return "expired"
    return "active"


def token_view(token: ApiToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.prefix,
        "scopes": list(token.scopes),
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "revoked_at": token.revoked_at,
        "last_used_at": token.last_used_at,
        "state": state(token),
        "webhook_secret": bool(token.webhook_secret),
    }


class TokenInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(min_length=1, max_length=len(SCOPES))
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    # A secret of its own to sign the webhooks of this token's requests, shown once like the token.
    webhook_secret: bool = False


@router.get("")
def list_tokens(user: CurrentUser, db: DB):
    tokens = db.scalars(select(ApiToken).where(ApiToken.owner_id == user.id).order_by(ApiToken.created_at.desc()))
    return [token_view(token) for token in tokens]


@router.post("", status_code=201)
def create_token(body: TokenInput, user: CurrentUser, db: DB):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Donnez un nom au jeton.")
    live = db.scalar(
        select(func.count()).select_from(ApiToken).where(ApiToken.owner_id == user.id, ApiToken.revoked_at.is_(None))
    )
    if live >= MAX_TOKENS:
        raise HTTPException(409, f"Au plus {MAX_TOKENS} jetons non révoqués par compte : révoquez-en un.")
    secret, prefix = new_token()
    signing = secrets.token_urlsafe(32) if body.webhook_secret else ""
    token = ApiToken(
        owner_id=user.id,
        name=name,
        token_hash=digest(secret),
        prefix=prefix,
        scopes=[scope for scope in SCOPES if scope in body.scopes],
        expires_at=time.time() + body.expires_in_days * 86400 if body.expires_in_days else None,
        webhook_secret=encrypt(signing) if signing else None,
    )
    db.add(token)
    db.flush()
    audit(
        db, owner_id=user.id, actor_id=user.id, action="api_token_created",
        token_id=token.id, name=token.name, prefix=prefix, scopes=token.scopes, expires_at=token.expires_at,
    )  # fmt: skip
    db.commit()
    # The only answer that ever carries the secret (and the webhook signing secret).
    return {**token_view(token), "token": secret, **({"webhook_secret": signing} if signing else {})}


@router.delete("/{token_id}")
def revoke_token(token_id: str, user: CurrentUser, db: DB):
    token = db.get(ApiToken, token_id)
    if not token or token.owner_id != user.id:
        raise HTTPException(404, "Jeton introuvable.")
    if not token.revoked_at:
        token.revoked_at = time.time()
        audit(db, owner_id=user.id, actor_id=user.id, action="api_token_revoked", token_id=token.id,
              name=token.name, prefix=token.prefix)  # fmt: skip
        db.commit()
    return token_view(token)


class RateLimiter:
    """Sliding one-minute window per token, in the memory of this process only."""

    def __init__(self):
        self.calls: dict[str, deque] = defaultdict(deque)
        self.guard = threading.Lock()

    def clear(self) -> None:
        with self.guard:
            self.calls.clear()

    def hit(self, key: str, limit: int, now: float | None = None) -> int:
        """0 when the call is admitted, otherwise the seconds to wait."""
        if limit <= 0:
            return 0
        now = time.monotonic() if now is None else now
        with self.guard:
            calls = self.calls[key]
            while calls and calls[0] <= now - 60:
                calls.popleft()
            if len(calls) >= limit:
                return max(1, int(calls[0] + 60 - now) + 1)
            calls.append(now)
            if len(self.calls) > 10000:
                for stale in [k for k, v in self.calls.items() if not v or v[-1] <= now - 60]:
                    del self.calls[stale]
            return 0


limiter = RateLimiter()


@dataclass
class Caller:
    user: User
    token: ApiToken


def unauthorized(code: str, message: str) -> HTTPException:
    return HTTPException(401, {"code": code, "message": message}, headers=CHALLENGE)


def authenticate(db, authorization: str | None) -> Caller:
    scheme, _, value = (authorization or "").partition(" ")
    value = value.strip()
    if scheme.casefold() != "bearer" or not value:
        raise unauthorized("missing_token", "Jeton d’API manquant : envoyez « Authorization: Bearer <jeton> ».")
    # The secret itself may contain "_": the prefix has a fixed length.
    size = len(PREFIX) + 8
    prefix = value[:size] if value.startswith(PREFIX) and value[size : size + 1] == "_" else ""
    wanted = digest(value)
    token = None
    for candidate in db.scalars(select(ApiToken).where(ApiToken.prefix == prefix)) if prefix else ():
        if hmac.compare_digest(candidate.token_hash, wanted):
            token = candidate
    if token is None:
        raise unauthorized("invalid_token", "Jeton d’API inconnu.")
    now = time.time()
    if token.revoked_at:
        raise unauthorized("revoked_token", "Ce jeton d’API a été révoqué.")
    if token.expires_at and token.expires_at <= now:
        raise unauthorized("expired_token", "Ce jeton d’API a expiré.")
    user = db.get(User, token.owner_id)
    if not user or not user.active:
        raise unauthorized("inactive_account", "Le compte de ce jeton est désactivé.")
    return Caller(user, token)


def require(scope: str):
    """Dependency: a valid Bearer token carrying `scope`, within its rate limit."""

    def dependency(db: DB, authorization: str | None = Header(default=None)) -> Caller:
        caller = authenticate(db, authorization)
        if scope not in caller.token.scopes:
            raise HTTPException(
                403, {"code": "insufficient_scope", "message": f"Ce jeton n’a pas la permission « {scope} ».",
                      "scope": scope},
            )  # fmt: skip
        wait = limiter.hit(caller.token.id, settings().api_rate_limit_per_minute)
        if wait:
            raise HTTPException(
                429, {"code": "rate_limited", "message": "Trop de requêtes pour ce jeton : réessayez plus tard."},
                headers={"Retry-After": str(wait)},
            )  # fmt: skip
        now = time.time()
        if not caller.token.last_used_at or now - caller.token.last_used_at >= USAGE_PRECISION:
            caller.token.last_used_at = now
            db.commit()
        return caller

    return dependency
