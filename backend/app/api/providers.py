from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from app.api.common import row
from app.models import Job, Project, Provider, RequestLog
from app.providers.codex import bridge_call
from app.providers.llm import llm
from app.providers.transports import NATIVE
from app.schemas import ProviderInput
from app.security import DB, Admin, CurrentUser, encrypt

router = APIRouter(prefix="/api/providers")


def public(provider: Provider) -> dict:
    return dict(row(provider, ("encrypted_key",)), has_api_key=bool(provider.encrypted_key))


@router.get("")
def providers(_user: CurrentUser, db: DB):
    return [public(p) for p in db.scalars(select(Provider).order_by(Provider.name))]


def require_key(kind: str, key: str) -> None:
    if kind in NATIVE and not key:
        raise HTTPException(422, "Une clé API est requise pour ce type de provider.")


@router.post("", status_code=201)
def create(body: ProviderInput, _admin: Admin, db: DB):
    require_key(body.kind, body.api_key or "")
    provider = Provider(**body.model_dump(exclude={"api_key"}), encrypted_key=encrypt(body.api_key or ""))
    db.add(provider)
    db.commit()
    return public(provider)


@router.put("/{provider_id}")
def update(provider_id: str, body: ProviderInput, _admin: Admin, db: DB):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider introuvable.")
    for key, value in body.model_dump(exclude={"api_key"}).items():
        setattr(provider, key, value)
    if body.api_key is not None:
        provider.encrypted_key = encrypt(body.api_key)
    if body.kind == "codex_chatgpt":
        provider.encrypted_key = ""
    require_key(body.kind, provider.encrypted_key)
    db.commit()
    return public(provider)


@router.post("/{provider_id}/test")
async def test(provider_id: str, _admin: Admin, db: DB):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider introuvable.")
    try:
        models = await llm.models(provider)
        return {
            "ok": True,
            "models": models,
            "message": "Liste des modèles accessible. Les capacités restent déclaratives.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "models": [],
            "message": f"GET /models indisponible ({type(exc).__name__}). "
            "La traduction peut fonctionner si cet endpoint n’existe pas ; vérifiez URL et authentification.",
        }


@router.delete("/{provider_id}")
def delete_provider(provider_id: str, _admin: Admin, db: DB):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider introuvable.")
    # Say what still points at the provider; the generic integrity message would not.
    books = db.scalar(select(func.count()).select_from(Project).where(Project.provider_id == provider_id))
    if books:
        raise HTTPException(
            409,
            f"Ce provider est encore sélectionné par {books} livre(s). "
            "Choisissez un autre provider pour ces livres avant de le supprimer.",
        )
    jobs = db.scalar(select(func.count()).select_from(Job).where(Job.provider_id == provider_id))
    requests = db.scalar(
        select(func.count()).select_from(RequestLog).where(RequestLog.provider_id == provider_id)
    )
    if jobs or requests:
        raise HTTPException(
            409,
            f"Ce provider ne peut pas être supprimé : {jobs} travail(aux) et {requests} requête(s) de "
            "l’historique s’y rapportent, et les statistiques de coût en dépendent. "
            "Il n’est plus utilisé par aucun livre : vous pouvez le renommer ou retirer sa clé API.",
        )
    db.delete(provider)
    db.commit()
    return {"ok": True}


@router.post("/{provider_id}/codex/{action}")
async def codex_account(
    provider_id: str, action: Literal["status", "login", "logout", "models"], _admin: Admin, db: DB
):
    provider = db.get(Provider, provider_id)
    if not provider or provider.kind != "codex_chatgpt":
        raise HTTPException(404, "Provider Codex ChatGPT introuvable.")
    try:
        return await bridge_call(provider_id, action)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502,
            f"Connexion Codex indisponible (HTTP {exc.response.status_code}). "
            "Vérifiez le connecteur et l’authentification ChatGPT.",
        ) from None
    except httpx.RequestError:
        raise HTTPException(
            502, "Le connecteur Codex ne répond pas. Démarrez le profil Docker codex."
        ) from None
