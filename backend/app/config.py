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
    allowed_origins: str = "http://localhost:8088,http://127.0.0.1:8088"
    session_duration_hours: int = Field(default=24, ge=1, le=24 * 90)
    max_upload_mb: int = 60
    max_unpacked_mb: int = 300
    max_entries: int = 5000
    openviking_url: str = ""
    openviking_api_key: str = ""
    searxng_url: str = ""
    final_review_enabled: bool = True
    openviking_root_uri: str = "viking://resources/epub-translator"
    epubcheck_jar: str = ""
    frontend_dir: Path = Path("/app/frontend/dist")
    codex_bridge_url: str = "http://codex:8092"
    codex_bridge_token: str = ""
    prompt_dir: Path = Path(__file__).resolve().parents[2] / "prompts"
    # The job lease lasts 60 s: the heartbeat that renews it must stay well below.
    worker_heartbeat_seconds: int = Field(default=2, ge=1, le=20)
    memory_catalog_interval_seconds: int = Field(default=60, ge=10, le=86400)
    provider_recovery_base_seconds: int = 60
    provider_recovery_max_seconds: int = 3600

    def prepare(self) -> None:
        for name in ("books", "projects", "exports"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        if len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY doit contenir au moins 32 caractères (voir .env.example).")


@lru_cache
def settings() -> Settings:
    return Settings()
