import asyncio
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from codex_bridge.rpc import (
    CodexAuthRequired,
    CodexContentRefused,
    CodexError,
    CodexQuotaExceeded,
    CodexSession,
    CodexUnavailable,
)

sessions: dict[str, CodexSession] = {}
running: dict[tuple[str, str], asyncio.Task] = {}


@asynccontextmanager
async def lifespan(_app):
    if len(os.environ.get("CODEX_BRIDGE_TOKEN", "")) < 32:
        raise RuntimeError("CODEX_BRIDGE_TOKEN manquant ou trop court.")
    yield
    for session in sessions.values():
        await session.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def authenticate(request: Request, call_next):
    expected = "Bearer " + os.environ.get("CODEX_BRIDGE_TOKEN", "")
    if request.headers.get("origin") or not hmac.compare_digest(
        request.headers.get("authorization", ""), expected
    ):
        return JSONResponse({"detail": "Accès refusé."}, status_code=401)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(CodexError)
async def codex_error(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(CodexUnavailable)
async def codex_unavailable(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(CodexQuotaExceeded)
async def quota_exceeded(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": "3600"})


@app.exception_handler(CodexContentRefused)
async def content_refused(_request, exc):
    return JSONResponse({"detail": str(exc), "code": "content_refusal"}, status_code=451)


@app.exception_handler(CodexAuthRequired)
async def auth_required(_request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=401)


@app.get("/health")
def health():
    return {"status": "ok", "codex_version": "0.154.0"}


@app.exception_handler(TimeoutError)
async def timed_out(_request, _exc):
    return JSONResponse({"detail": "Délai Codex dépassé."}, status_code=504)


async def session_for(pid: UUID) -> CodexSession:
    key = str(pid)
    if key not in sessions:
        sessions[key] = CodexSession(Path("/state") / key)
    session = sessions[key]
    await session.start()
    return session


async def account(pid: UUID, action: Literal["status", "login", "logout", "models"]):
    session = await session_for(pid)
    if action in {"login", "logout"}:
        async with session.generation_lock:
            if session.active_generations:
                raise HTTPException(
                    409,
                    "Des générations Codex sont actives. Mettez les projets en pause avant de modifier le compte.",
                )
            if action == "logout":
                await session.call("account/logout", {})
                return {"connected": False}
            result = await session.call("account/login/start", {"type": "chatgptDeviceCode"})
            return {
                key: result[key]
                for key in ("type", "loginId", "verificationUrl", "userCode")
                if key in result
            }
    if action == "status":
        result = await session.call("account/read", {"refreshToken": False})
        account = result.get("account")
        return {
            "connected": bool(account),
            "type": account.get("type") if account else None,
            "plan": account.get("planType") if account else None,
        }
    result = await session.call("model/list", {"includeHidden": False, "limit": 100})
    return {
        "models": [m["model"] for m in result.get("data", [])],
        "details": [
            {
                "model": m["model"],
                "default": m.get("isDefault", False),
                "reasoning": m.get("supportedReasoningEfforts", []),
            }
            for m in result.get("data", [])
        ],
    }


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=200)
    messages: list[dict]
    output_schema: dict = Field(alias="schema")
    timeout: int = Field(ge=5, le=3600)
    effort: str | None = None
    output_reservation: int = Field(ge=256, le=200000)
    context_window: int = Field(ge=2048, le=2000000)
    request_id: UUID = Field(default_factory=uuid4)


# Registered before the dynamic account route: see route ordering below.
async def complete(pid: UUID, body: Completion, request: Request):
    if len(str(body.messages).encode()) > 2_000_000:
        raise HTTPException(413, "Contexte trop volumineux.")
    key = (str(pid), str(body.request_id))
    if key in running:
        raise HTTPException(409, "Cette requête est déjà active.")

    async def generate():
        session = await session_for(pid)
        return await session.complete(body.model_dump(by_alias=True))

    async def disconnected():
        while not await request.is_disconnected():
            await asyncio.sleep(0.5)

    task = asyncio.create_task(generate())
    watcher = asyncio.create_task(disconnected())
    running[key] = task
    try:
        async with asyncio.timeout(max(1, body.timeout - 3)):
            done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                raise HTTPException(499, "Client déconnecté ; interruption demandée.")
            try:
                return await task
            except asyncio.CancelledError:
                raise HTTPException(409, "Requête Codex interrompue.") from None
    finally:
        running.pop(key, None)
        watcher.cancel()
        task.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


class Interrupt(BaseModel):
    request_id: UUID


async def interrupt(pid: UUID, body: Interrupt):
    task = running.get((str(pid), str(body.request_id)))
    if task:
        task.cancel()
    return {"interruption_requested": task is not None}


app.add_api_route("/providers/{pid}/complete", complete, methods=["POST"])
app.add_api_route("/providers/{pid}/interrupt", interrupt, methods=["POST"])
app.add_api_route("/providers/{pid}/{action}", account, methods=["POST"])
