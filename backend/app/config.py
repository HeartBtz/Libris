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
    # Two-phase imports: files kept for inspection before the commit (DATA_DIR/staging).
    import_max_files: int = Field(default=500, ge=1, le=5000)
    import_max_session_mb: int = Field(default=2048, ge=1)
    import_session_hours: int = Field(default=24, ge=1, le=24 * 30)
    # Characters of one text chapter (TXT file or JSON chapter), after decoding.
    text_chapter_max_chars: int = Field(default=2_000_000, ge=1000, le=50_000_000)
    # Automation API (/api/v1): JSON body of one request (empty: MAX_UPLOAD_MB), chapters per request,
    # and calls per token and per minute in each API process (0: no limit).
    api_max_payload_mb: int | None = Field(default=None, ge=1, le=4096)
    api_max_chapters: int = Field(default=2000, ge=1, le=100_000)
    api_rate_limit_per_minute: int = Field(default=120, ge=0, le=100_000)
    # Delivery of automation requests (app.engines.delivery). Longest `?wait=` a client may ask for.
    api_result_max_wait_seconds: int = Field(default=60, ge=0, le=600)
    # A request whose job stays paused, blocked or waiting this long fails (with the reason), and so
    # does one still unfinished after api_request_max_hours: no request stays `running` forever.
    api_request_stall_minutes: int = Field(default=360, ge=1, le=60 * 24 * 30)
    api_request_max_hours: int = Field(default=168, ge=1, le=24 * 365)
    # EPUBCheck failures of a delivered EPUB: rounds of automatic repair before the request fails.
    delivery_repair_attempts: int = Field(default=3, ge=1, le=10)
    # Webhooks: hosts a callback_url may name (comma-separated, `*.example.org` allowed; empty:
    # webhooks refused), private networks allowed anyway (CIDRs), global HMAC secret (a token's own
    # secret wins), attempts and timeout of each call.
    api_webhook_hosts: str = ""
    api_webhook_private_networks: str = ""
    api_webhook_secret: str = ""
    api_webhook_max_attempts: int = Field(default=6, ge=1, le=20)
    api_webhook_timeout_seconds: int = Field(default=10, ge=1, le=60)
    # Two-phase imports: a volume or chapter number guessed with low confidence is accepted (and the
    # reason recorded) unless this asks the person to confirm it.
    import_confirm_low_confidence: bool = False
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
    # Passages of one book translated or reviewed at once; 0 follows the provider's max_concurrency,
    # 1 restores strictly sequential processing.
    worker_book_parallelism: int = Field(default=0, ge=0, le=16)
    memory_catalog_interval_seconds: int = Field(default=60, ge=10, le=86400)
    provider_recovery_base_seconds: int = 60
    provider_recovery_max_seconds: int = 3600
    # Autopilot: a launched book goes from its source to an output without anyone's action.
    # Default of new launches (UI and API); a project's config["autopilot"] or a launch can override it.
    autopilot_enabled: bool = True
    # Rounds of recovery → final review → AI arbitration before the remaining points are settled.
    autopilot_max_rounds: int = Field(default=3, ge=1, le=10)
    # Providers tried, in order, when the job's own one is down or fails a passage: names or ids,
    # comma-separated. A project's config["fallback_provider_ids"] comes first.
    autopilot_fallback_providers: str = ""
    # A provider outage is waited out this many times and this long at most before the next provider.
    autopilot_outage_max_retries: int = Field(default=5, ge=1, le=100)
    autopilot_outage_max_wait_seconds: int = Field(default=3600, ge=0, le=7 * 86400)
    # Confidence (0-1) an automatic memory decision needs; below it the proposal is rejected (glossary,
    # series identity) or left as it is (Book Bible, outdated chapter context).
    autopilot_glossary_min_confidence: float = Field(default=0.75, ge=0, le=1)
    autopilot_identity_min_confidence: float = Field(default=0.8, ge=0, le=1)
    autopilot_bible_min_coverage: float = Field(default=0.8, ge=0, le=1)
    autopilot_stale_min_coverage: float = Field(default=0.5, ge=0, le=1)
    # Diagnostic data is bounded by the worker (app.maintenance.retention); 0 disables a rule.
    retention_request_bodies_days: int = Field(default=30, ge=0)
    retention_events_days: int = Field(default=7, ge=0)
    retention_outbox_sent_days: int = Field(default=7, ge=0)
    retention_bible_revisions: int = Field(default=20, ge=0)
    retention_job_state_days: int = Field(default=30, ge=0)
    # Empty: GET /metrics does not exist. Set: Prometheus must send it as a Bearer token.
    metrics_token: str = ""

    @property
    def api_payload_mb(self) -> int:
        return self.api_max_payload_mb or self.max_upload_mb

    def prepare(self) -> None:
        for name in ("books", "projects", "exports", "sources", "staging", "results"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)
        if len(self.secret_key) < 32:
            raise RuntimeError("SECRET_KEY doit contenir au moins 32 caractères (voir .env.example).")
        if self.api_webhook_secret and len(self.api_webhook_secret) < 32:
            raise RuntimeError("API_WEBHOOK_SECRET doit contenir au moins 32 caractères, ou rester vide.")
        if self.metrics_token and len(self.metrics_token) < 24:
            raise RuntimeError("METRICS_TOKEN doit contenir au moins 24 caractères, ou rester vide.")


@lru_cache
def settings() -> Settings:
    return Settings()
