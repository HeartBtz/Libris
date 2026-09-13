import logging
import time
import zipfile
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from lxml import etree
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api import (
    characters,
    coverage,
    exports,
    identity,
    memory,
    observability,
    projects,
    providers,
    segments,
)
from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.models.common import uid
from app.providers.llm import LLMError
from app.security import password_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config = settings()
    config.prepare()
    with SessionLocal() as db:
        if not db.scalar(select(User.id).limit(1)):
            if len(config.bootstrap_password) < 12:
                raise RuntimeError(
                    "Définissez BOOTSTRAP_PASSWORD (12 caractères minimum) pour le premier compte."
                )
            db.add(
                User(
                    username=config.bootstrap_username,
                    password_hash=password_hash(config.bootstrap_password),
                    admin=True,
                )
            )
            db.commit()
    yield


app = FastAPI(title="Libris", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)
login_attempts: dict[str, deque] = defaultdict(deque)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings().allowed_origins.split(","):
            return JSONResponse(
                {"detail": "Origine non autorisée. Configurez ALLOWED_ORIGINS."}, status_code=403
            )
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Requête intersite refusée."}, status_code=403)
    if request.url.path == "/api/auth/login" and request.method == "POST":
        key = request.client.host if request.client else "local"
        attempts = login_attempts[key]
        now = time.monotonic()
        while attempts and attempts[0] < now - 300:
            attempts.popleft()
        if len(attempts) >= 20:
            return JSONResponse(
                {"detail": "Trop de tentatives. Réessayez dans cinq minutes."}, status_code=429
            )
        attempts.append(now)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def invalid(_request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)[:1500]}, status_code=422)


@app.exception_handler(IntegrityError)
async def conflict(_request: Request, _exc: IntegrityError):
    return JSONResponse(
        {"detail": "Cette entrée existe déjà, ou une référence est invalide."}, status_code=409
    )


@app.exception_handler(LLMError)
async def llm_failure(_request: Request, exc: LLMError):
    return JSONResponse({"detail": str(exc)}, status_code=502)


@app.exception_handler(zipfile.BadZipFile)
@app.exception_handler(etree.XMLSyntaxError)
async def invalid_archive(_request: Request, exc: Exception):
    return JSONResponse({"detail": f"Archive EPUB ou XML invalide ({type(exc).__name__})."}, status_code=422)


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception):
    reference = uid()
    logging.getLogger("epub.api").error(
        "operation=api status=failed reference=%s error_type=%s", reference, type(exc).__name__
    )
    return JSONResponse(
        {
            "detail": f"L’opération a échoué côté serveur. Référence de diagnostic : {reference}. "
            "Les traductions déjà enregistrées sont conservées."
        },
        status_code=500,
    )


for module in (identity, providers, exports, projects, segments, memory, observability, characters, coverage):
    app.include_router(module.router)


@app.get("/health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "version": "0.1.0"}


if (settings().frontend_dir / "assets").exists():
    app.mount("/assets", StaticFiles(directory=settings().frontend_dir / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "Route API inconnue."}, status_code=404)
    index = settings().frontend_dir / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "API prête. Compilez le frontend ou utilisez Docker Compose."})
