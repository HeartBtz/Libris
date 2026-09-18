from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:////data/app.db"
    data_dir: Path = Path("/data")
    secret_key: str = ""
    bootstrap_password: str = ""
    bootstrap_username: str = "admin"
    cookie_secure: bool = False  # Set to True in production; tests need False
    openapi_enabled: bool = True  # /openapi.json, for signed-in users only
    allowed_origins: str = "http://localhost:8088,http://127.0.0.1:8088"
    session_duration_hours: int = Field(default=24, ge=1, le=24 * 90)
    max_upload_mb: int = 60
    max_unpacked_mb: int = 300
    max_entries: int = 5000
    # Whole-archive compression ratio above which an EPUB is refused as a possible zip bomb.
    max_compression_ratio: int = Field(default=100, ge=10, le=100000)
    # Memory kept for unpacked books of recent chapter previews; 0 disables the cache.
    preview_cache_mb: int = Field(default=64, ge=0, le=4096)
    openviking_url: str = ""
    openviking_api_key: str = ""
    searxng_url: str = ""
    final_review_enabled: bool = True
    openviking_root_uri: str = "viking://resources/epub-translator"
    epubcheck_jar: str = ""
    # Live event streams (SSE) held open at once, per account and for the whole API process.
    event_streams_per_user: int = Field(default=4, ge=1, le=100)
    event_streams_total: int = Field(default=100, ge=1, le=10000)
    epubcheck_concurrency: int = Field(default=2, ge=1, le=16)
    epubcheck_max_heap_mb: int = Field(default=1024, ge=128, le=16384)
    frontend_dir: Path = Path("/app/frontend/dist")
    codex_bridge_url: str = "http://codex:8092"
    codex_bridge_token: str = ""
    prompt_dir: Path = Path(__file__).resolve().parents[2] / "prompts"
    # The job lease lasts 60 s: the heartbeat that renews it must stay well below.
    worker_heartbeat_seconds: int = Field(default=2, ge=1, le=20)
    memory_catalog_interval_seconds: int = Field(default=60, ge=10, le=86400)
    provider_recovery_base_seconds: int = 60
    provider_recovery_max_seconds: int = 3600
    # Diagnostic data is bounded by the worker (app.maintenance.retention); 0 disables a rule.
    retention_request_bodies_days: int = Field(default=30, ge=0)
    retention_events_days: int = Field(default=7, ge=0)
    retention_outbox_sent_days: int = Field(default=7, ge=0)
    retention_bible_revisions: int = Field(default=20, ge=0)

    def prepare(self) -> None:
        for name in ("books", "projects", "exports"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        if len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY doit contenir au moins 32 caractères (voir .env.example).")


@lru_cache
def settings() -> Settings:
    return Settings()
