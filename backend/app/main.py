import logging
import zipfile
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from lxml import etree
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app import __version__, throttle
from app.api import (
    characters,
    coverage,
    estimates,
    exports,
    identity,
    imports,
    memory,
    monitoring,
    observability,
    projects,
    providers,
    recovery,
    segments,
    series,
)
from app.config import settings
from app.db import SessionLocal
from app.diagnostics import safe_trace
from app.i18n import english, localize, preferred_language
from app.limits import BodyLimit
from app.models import User
from app.models.common import uid
from app.providers.llm import LLMError
from app.security import CurrentUser, password_hash

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


app = FastAPI(
    title="Libris", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)
login_attempts = throttle.attempts  # kept for callers that reset the throttle
app.add_middleware(BodyLimit)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin not in settings().allowed_origins.split(","):
            return error(request, "Origine non autorisée. Configurez ALLOWED_ORIGINS.", 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return error(request, "Requête intersite refusée.", 403)
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


def error(request: Request, detail, status: int, headers: dict | None = None) -> JSONResponse:
    # Messages are written in French; an interface set to English receives them in English.
    language = preferred_language(request.headers.get("accept-language"))
    return JSONResponse({"detail": localize(detail, language)}, status_code=status, headers=headers)


@app.exception_handler(HTTPException)
async def refused(request: Request, exc: HTTPException):
    return error(request, exc.detail, exc.status_code, exc.headers)


@app.exception_handler(RequestValidationError)
async def malformed(request: Request, exc: RequestValidationError):
    errors = jsonable_encoder(exc.errors())
    if preferred_language(request.headers.get("accept-language")) == "en":
        prefix = "Value error, "
        for item in errors:
            message = str(item.get("msg", ""))
            if message.startswith(prefix):
                item["msg"] = prefix + (english(message[len(prefix) :]) or message[len(prefix) :])
    return JSONResponse({"detail": errors}, status_code=422)


@app.exception_handler(ValueError)
async def invalid(request: Request, exc: ValueError):
    return error(request, str(exc)[:1500], 422)


@app.exception_handler(IntegrityError)
async def conflict(request: Request, _exc: IntegrityError):
    return error(request, "Cette entrée existe déjà, ou une référence est invalide.", 409)


@app.exception_handler(LLMError)
async def llm_failure(request: Request, exc: LLMError):
    return error(request, str(exc), 502)


@app.exception_handler(httpx.RequestError)
async def upstream_unreachable(request: Request, exc: httpx.RequestError):
    # Connection refused, DNS failure or timeout towards OpenViking, the Codex bridge, SearXNG or a provider.
    return error(
        request,
        f"Un service externe requis par cette action est injoignable ({type(exc).__name__}). "
        "Vérifiez son adresse et qu’il est démarré, puis réessayez.",
        502,
    )


@app.exception_handler(zipfile.BadZipFile)
@app.exception_handler(etree.XMLSyntaxError)
async def invalid_archive(request: Request, exc: Exception):
    return error(request, f"Archive EPUB ou XML invalide ({type(exc).__name__}).", 422)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    reference = uid()
    logging.getLogger("epub.api").error(
        "operation=api status=failed reference=%s error_type=%s trace=%s",
        reference,
        type(exc).__name__,
        safe_trace(exc),
    )
    return error(
        request,
        f"L’opération a échoué côté serveur. Référence de diagnostic : {reference}. "
        "Les traductions déjà enregistrées sont conservées.",
        500,
    )


for module in (
    identity, providers, recovery, exports, projects, segments, memory, observability, characters, coverage,
    series, imports,
):  # fmt: skip
    app.include_router(module.router)
app.include_router(estimates.router)
app.include_router(monitoring.router)


@app.get("/openapi.json", include_in_schema=False)
def openapi_schema(_user: CurrentUser):
    # The API map is for signed-in users only, and can be switched off entirely (OPENAPI_ENABLED).
    if not settings().openapi_enabled:
        raise HTTPException(404, "Route API inconnue.")
    return app.openapi()


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
        raise HTTPException(404, "Route API inconnue.")
    index = settings().frontend_dir / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "API prête. Compilez le frontend ou utilisez Docker Compose."})
