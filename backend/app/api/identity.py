import hashlib
import secrets
import time

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import delete, select

from app.api.common import row
from app.config import settings
from app.models import LoginSession, User
from app.schemas import Credentials
from app.security import DB, Admin, CurrentUser, password_hash, password_matches

router = APIRouter(prefix="/api")


@router.post("/auth/login")
def login(body: Credentials, response: Response, db: DB):
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not password_matches(body.password, user.password_hash):
        raise HTTPException(401, "Identifiants incorrects.")
    token = secrets.token_urlsafe(40)
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
def me(user: CurrentUser):
    return row(user, ("password_hash",))


@router.post("/auth/logout")
def logout(response: Response, user: CurrentUser, db: DB):
    db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    db.commit()
    response.delete_cookie("epub_session", path="/")
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
