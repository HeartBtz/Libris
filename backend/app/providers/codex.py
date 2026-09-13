"""Client of our private Codex adapter. OAuth credentials never enter the main application."""

import httpx

from app.config import settings


async def bridge_call(provider_id: str, action: str, payload: dict | None = None, timeout: int = 30) -> dict:
    config = settings()
    if not config.codex_bridge_token:
        raise ValueError(
            "Connecteur Codex non activé. Exécutez scripts/enable_codex.py puis redémarrez Compose."
        )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = await client.post(
            f"{config.codex_bridge_url}/providers/{provider_id}/{action}",
            headers={"Authorization": f"Bearer {config.codex_bridge_token}"},
            json=payload or {},
        )
        response.raise_for_status()
        return response.json()
