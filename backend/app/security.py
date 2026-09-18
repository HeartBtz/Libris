import base64
import hashlib
import hmac
import secrets
import time
from typing import Annotated

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import LoginSession, Membership, Project, User

DB = Annotated[Session, Depends(get_db)]


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return salt + ":" + value


def password_matches(password: str, stored: str) -> bool:
    return hmac.compare_digest(password_hash(password, stored.split(":")[0]), stored)


def cipher() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings().secret_key.encode()).digest()))


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode() if value else ""


class SecretUnreadable(Exception):
    """A stored secret was encrypted with another SECRET_KEY (restored database, rotated key)."""


def decrypt(value: str) -> str:
    try:
        return cipher().decrypt(value.encode()).decode() if value else ""
    except InvalidToken as exc:
        raise SecretUnreadable from exc


def current_user(db: DB, epub_session: str | None = Cookie(default=None)) -> User:
    token = hashlib.sha256((epub_session or "").encode()).hexdigest()
    session = db.get(LoginSession, token)
    if not session or session.expires_at < time.time():
        raise HTTPException(401, "Connexion nécessaire.")
    user = db.get(User, session.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Compte introuvable.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def admin_user(user: CurrentUser) -> User:
    if not user.admin:
        raise HTTPException(403, "Cette action nécessite un administrateur.")
    return user


Admin = Annotated[User, Depends(admin_user)]


def access(db: Session, project_id: str, user: User, write: bool = False, owner: bool = False) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Projet introuvable.")
    if project.owner_id == user.id:
        return project
    member = db.get(Membership, (project_id, user.id))
    if not member or owner or (write and member.role != "editor"):
        raise HTTPException(404, "Projet introuvable ou accès insuffisant.")
    return project
