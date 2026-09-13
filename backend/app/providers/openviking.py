"""Small async adapter for the documented OpenViking HTTP API, not a server dependency."""

from urllib.parse import unquote

import httpx

from app.engines.context.config import memory_config
from app.models import Outbox, Project


def validate_root(uri: str) -> str:
    if not uri.startswith("viking://resources/") or uri.rstrip("/") == "viking://resources":
        raise ValueError("Utilisez un sous-répertoire dédié sous viking://resources/.")
    path = unquote(uri[len("viking://resources/") :]).rstrip("/")
    if any(part in {"", ".", ".."} for part in path.split("/")) or any(c in path for c in "\\?#\x00"):
        raise ValueError("URI OpenViking non sûre.")
    return "viking://resources/" + path


def project_uri(project: Project) -> str:
    return f"{validate_root(memory_config()['root_uri'])}/{project.owner_id}/{project.id}"


def event_uri(project: Project, event: Outbox) -> str:
    # All dynamic identifiers originate from canonical SQL UUIDs, never book-provided paths.
    return f"{project_uri(project)}/events/{event.id}.json"


class OpenVikingClient:
    def __init__(self, config: dict):
        if not config["base_url"]:
            raise ValueError("URL OpenViking non configurée.")
        headers = {"X-API-Key": config["api_key"]} if config.get("api_key") else {}
        if config.get("auth_mode") == "trusted":
            if not config.get("account") or not config.get("user"):
                raise ValueError("Identités account/user requises pour le mode trusted.")
            headers.update({"X-OpenViking-Account": config["account"], "X-OpenViking-User": config["user"]})
        self.http = httpx.AsyncClient(
            base_url=config["base_url"].rstrip("/"),
            headers=headers,
            timeout=config["timeout"],
            trust_env=False,
            follow_redirects=False,
        )
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        response.raise_for_status()
        value = response.json()
        if value.get("status") not in {"ok", "success"}:
            raise ValueError("OpenViking a signalé une erreur applicative.")
        return value.get("result")

    async def write(self, uri: str, content: str, processing_mode: str | None = None):
        # Idempotent overwrite of one event. wait=False: index freshness never blocks translation.
        payload = {"uri": uri, "content": content, "mode": "replace", "wait": False}
        if processing_mode:
            payload["processing_mode"] = processing_mode
        try:
            return await self.request("POST", "/api/v1/content/write", json=payload)
        except httpx.HTTPStatusError as exc:
            if (
                processing_mode
                and exc.response.status_code in (400, 422)
                and "processing_mode" in exc.response.text
            ):
                return await self.write(uri, content)
            if exc.response.status_code != 404:
                raise
        # Older deployments require explicit create. A stable event URI keeps retries idempotent.
        try:
            return await self.request("POST", "/api/v1/content/write", json={**payload, "mode": "create"})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
            return await self.request("POST", "/api/v1/content/write", json=payload)

    async def read(self, uri: str) -> str:
        result = await self.request("GET", "/api/v1/content/read", params={"uri": uri})
        if not isinstance(result, str):
            raise ValueError("Réponse read OpenViking inattendue.")
        return result

    async def find(self, query: str, root: str, deep: bool = False, limit: int = 12) -> list[dict]:
        payload = {
            "query": query[:9000],
            "target_uri": root,
            "limit": limit,
            "level": [2],
            "score_threshold": self.config["min_score"],
        }
        # mode=context cannot be URI-scoped on this API. Never use unscoped context assembly.
        if deep:
            payload["mode"] = "list"
        result = await self.request("POST", "/api/v1/search/" + ("search" if deep else "find"), json=payload)
        if not isinstance(result, dict):
            raise ValueError("Réponse search OpenViking inattendue.")
        return [hit for hit in result.get("resources", []) if isinstance(hit, dict)]

    async def reindex(self, uri: str):
        return await self.request(
            "POST", "/api/v1/content/reindex", json={"uri": uri, "wait": False, "mode": "vectors_only"}
        )
