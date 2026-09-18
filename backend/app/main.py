import logging
import zipfile
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from lxml import etree
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app import __version__, throttle
from app.api import (
    characters,
    coverage,
    exports,
    identity,
    memory,
    monitoring,
    observability,
    projects,
    providers,
    recovery,
    segments,
)
from app.config import settings
from app.db import SessionLocal
from app.diagnostics import safe_trace
from app.limits import BodyLimit
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


app = FastAPI(title="Libris", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
login_attempts = throttle.attempts  # kept for callers that reset the throttle
app.add_middleware(BodyLimit)


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
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
        "frame-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
    )
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


@app.exception_handler(httpx.RequestError)
async def upstream_unreachable(_request: Request, exc: httpx.RequestError):
    # Connection refused, DNS failure or timeout towards OpenViking, the Codex bridge, SearXNG or a provider.
    return JSONResponse(
        {
            "detail": f"Un service externe requis par cette action est injoignable ({type(exc).__name__}). "
            "Vérifiez son adresse et qu’il est démarré, puis réessayez."
        },
        status_code=502,
    )


@app.exception_handler(zipfile.BadZipFile)
@app.exception_handler(etree.XMLSyntaxError)
async def invalid_archive(_request: Request, exc: Exception):
    return JSONResponse({"detail": f"Archive EPUB ou XML invalide ({type(exc).__name__})."}, status_code=422)


@app.exception_handler(Exception)
async def unexpected(_request: Request, exc: Exception):
    reference = uid()
    logging.getLogger("epub.api").error(
        "operation=api status=failed reference=%s error_type=%s trace=%s",
        reference,
        type(exc).__name__,
        safe_trace(exc),
    )
    return JSONResponse(
        {
            "detail": f"L’opération a échoué côté serveur. Référence de diagnostic : {reference}. "
            "Les traductions déjà enregistrées sont conservées."
        },
        status_code=500,
    )


for module in (identity, providers, recovery, exports, projects, segments, memory, observability, characters, coverage):
    app.include_router(module.router)
app.include_router(monitoring.router)


@app.get("/health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}


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
