"""Small async adapter for the documented OpenViking HTTP API, not a server dependency."""

import re
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


# 1: <root>/<owner>/<project>; 2: series and standalone spaces (Libris 0.6).
LAYOUT_VERSION = 2
MEMORY_KINDS = ("analysis", "narrative", "human_decision")


def owner_uri(owner_id: str) -> str:
    return f"{validate_root(memory_config()['root_uri'])}/{owner_id}"


def series_uri(owner_id: str, series_id: str) -> str:
    return f"{owner_uri(owner_id)}/series/{series_id}"


def project_uri(project: Project) -> str:
    """A volume of a series lives in the series space; a standalone volume in its own."""
    # All dynamic identifiers originate from canonical SQL UUIDs, never book-provided paths.
    if project.series_id:
        return f"{series_uri(project.owner_id, project.series_id)}/volumes/{project.id}"
    return f"{owner_uri(project.owner_id)}/standalone/{project.id}"


def search_root(project: Project) -> str:
    """Where a passage's search looks: the whole series (earlier volumes included), or the volume.

    The directory only bounds the server-side search: every hit is still checked against the exact
    list of events SQL admits for this passage.
    """
    if project.series_id:
        return f"{series_uri(project.owner_id, project.series_id)}/volumes"
    return f"{project_uri(project)}/events"


def memory_event_uri(project: Project, memory_id: str) -> str:
    return f"{project_uri(project)}/events/{memory_id}.json"


def event_uri(project: Project, event: Outbox) -> str:
    # A memory event is named after its SQL memory, so it can be rebuilt from SQL alone.
    if event.payload.get("type") in MEMORY_KINDS:
        return memory_event_uri(project, event.event_key)
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

    async def ls(self, uri: str, recursive: bool = False, limit: int = 1000) -> list[dict] | None:
        """Entries of a directory ({"uri", "isDir"}), or None when it does not exist."""
        params = {"uri": uri, "recursive": recursive, "output": "original", "node_limit": limit}
        try:
            result = await self.request("GET", "/api/v1/fs/ls", params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        if not isinstance(result, list):
            raise ValueError("Réponse ls OpenViking inattendue.")
        entries = []
        for entry in result:
            if isinstance(entry, str):
                entries.append({"uri": entry, "isDir": entry.endswith("/")})
            elif isinstance(entry, dict) and isinstance(entry.get("uri"), str):
                entries.append({"uri": entry["uri"], "isDir": bool(entry.get("isDir"))})
        return entries

    async def remove_tree(self, uri: str) -> dict | None:
        """Removes a whole directory. Only a prefix Libris owns (see cleanup_scope) is ever accepted."""
        if cleanup_scope(uri, self.config.get("root_uri", "")) is None:
            raise ValueError("URI OpenViking non sûre.")
        try:
            result = await self.request("DELETE", "/api/v1/fs", params={"uri": uri, "recursive": True})
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return result if isinstance(result, dict) else {}


# The only directories a cleanup may remove, relative to the root: a series, a volume in its series
# space or its standalone space, and a volume of the 0.5 layout <root>/<owner>/<project>.
_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
LIBRIS_ID = re.compile(_ID)
_SCOPES = (
    ("series", re.compile(rf"({_ID})/series/({_ID})")),
    ("series_volume", re.compile(rf"({_ID})/series/({_ID})/volumes/({_ID})")),
    ("standalone", re.compile(rf"({_ID})/standalone/({_ID})")),
    ("legacy", re.compile(rf"({_ID})/({_ID})")),
)


def cleanup_scope(uri: str, root: str) -> tuple[str, tuple[str, ...]] | None:
    """(kind, ids) when `uri` is exactly one of Libris' item directories under `root`, else None."""
    try:
        root = validate_root(root)
    except ValueError:
        return None
    if not uri.startswith(root + "/"):
        return None
    relative = uri[len(root) + 1 :]
    for kind, pattern in _SCOPES:
        match = pattern.fullmatch(relative)
        if match:
            return kind, match.groups()
    return None
