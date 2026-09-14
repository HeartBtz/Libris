import hashlib
import secrets
import time

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.common import row
from app.config import settings
from app.models import LoginSession, User
from app.schemas import Credentials
from app.security import DB, Admin, CurrentUser, password_hash, password_matches

router = APIRouter(prefix="/api")

# Equal-cost password verification for unknown and inactive accounts.
_dummy_password = password_hash(secrets.token_urlsafe(32))


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class PasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=200)


class AccountUpdate(BaseModel):
    admin: bool
    active: bool


@router.post("/auth/login")
def login(body: Credentials, response: Response, db: DB):
    user = db.scalar(select(User).where(User.username == body.username))
    matches = password_matches(body.password, user.password_hash if user else _dummy_password)
    if not user or not matches or not user.active:
        raise HTTPException(401, "Identifiants incorrects.")
    token = secrets.token_urlsafe(40)
    db.execute(delete(LoginSession).where(LoginSession.expires_at < time.time()))
    db.add(
        LoginSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            user_id=user.id,
            expires_at=time.time() + 43200,
        )
    )
    db.commit()
    response.set_cookie(
        "epub_session",
        token,
        httponly=True,
        samesite="strict",
        secure=settings().cookie_secure,
        max_age=43200,
        path="/",
    )
    return row(user, ("password_hash",))


@router.get("/auth/me")
def me(response: Response, user: CurrentUser, db: DB, epub_session: str = Cookie(default="")):
    if settings().cookie_secure:
        session = db.get(LoginSession, hashlib.sha256(epub_session.encode()).hexdigest())
        if not session:
            raise HTTPException(401, "Connexion nécessaire.")
        response.set_cookie(
            "epub_session", epub_session, httponly=True, samesite="strict", secure=True,
            max_age=max(0, int(session.expires_at - time.time())), path="/",
        )
    return row(user, ("password_hash",))


@router.post("/auth/logout")
def logout(response: Response, user: CurrentUser, db: DB, epub_session: str = Cookie(default="")):
    db.execute(delete(LoginSession).where(
        LoginSession.user_id == user.id,
        LoginSession.token_hash == hashlib.sha256(epub_session.encode()).hexdigest(),
    ))
    db.commit()
    response.delete_cookie("epub_session", path="/")
    return {"ok": True}


@router.put("/auth/password")
def change_password(body: PasswordChange, response: Response, user: CurrentUser, db: DB):
    if not password_matches(body.current_password, user.password_hash):
        raise HTTPException(400, "Mot de passe actuel incorrect.")
    user.password_hash = password_hash(body.new_password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()
    response.delete_cookie("epub_session", path="/")
    return {"ok": True}


@router.get("/auth/sessions")
def sessions(user: CurrentUser, db: DB, epub_session: str = Cookie(default="")):
    current = hashlib.sha256(epub_session.encode()).hexdigest()
    return [
        {"id": s.token_hash, "expires_at": s.expires_at, "current": s.token_hash == current}
        for s in db.scalars(select(LoginSession).where(
            LoginSession.user_id == user.id, LoginSession.expires_at > time.time(),
        ).order_by(LoginSession.expires_at.desc()))
    ]


@router.delete("/auth/sessions/{session_id}")
def revoke_session(session_id: str, user: CurrentUser, db: DB):
    db.execute(delete(LoginSession).where(
        LoginSession.user_id == user.id, LoginSession.token_hash == session_id,
    ))
    db.commit()
    return {"ok": True}


@router.put("/users/{user_id}")
def update_user(user_id: str, body: AccountUpdate, admin: Admin, db: DB):
    # Lock in stable order: concurrent demotions must not remove every active administrator.
    accounts = list(db.scalars(select(User).order_by(User.id).with_for_update()))
    target = next((u for u in accounts if u.id == user_id), None)
    if not target:
        raise HTTPException(404, "Compte introuvable.")
    if target.id == admin.id and (not body.active or not body.admin):
        raise HTTPException(409, "Vous ne pouvez pas retirer votre propre accès administrateur.")
    if target.admin and target.active and not (body.active and body.admin):
        if not any(u.id != target.id and u.active and u.admin for u in accounts):
            raise HTTPException(409, "Au moins un administrateur actif est nécessaire.")
    target.admin, target.active = body.admin, body.active
    db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
    db.commit()
    return row(target, ("password_hash",))


@router.put("/users/{user_id}/password")
def reset_password(user_id: str, body: PasswordReset, _admin: Admin, db: DB):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Compte introuvable.")
    target.password_hash = password_hash(body.password)
    db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
    db.commit()
    return {"ok": True}


@router.get("/users")
def users(_admin: Admin, db: DB):
    return [row(u, ("password_hash",)) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", status_code=201)
def create_user(body: Credentials, _admin: Admin, db: DB):
    user = User(username=body.username, password_hash=password_hash(body.password))
    db.add(user)
    db.commit()
    return row(user, ("password_hash",))
