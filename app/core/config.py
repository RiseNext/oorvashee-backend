"""Application settings — loaded once at boot from environment.

All config access goes through `get_settings()`. Never read os.environ directly
in application code.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# `NoDecode` opts out of pydantic-settings' default JSON parsing for complex
# types. Our env values are plain comma-separated strings, parsed by the
# `_parse_csv` field_validator at `mode="before"`.
CsvStr = Annotated[list[str], NoDecode, Field(default_factory=list)]


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    """Single source of truth for runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: AppEnv = AppEnv.LOCAL
    app_name: str = "oorvashee-backend"
    app_version: str = "0.1.0"
    app_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    frontend_base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:3000")
    allowed_origins: CsvStr
    log_level: LogLevel = LogLevel.INFO
    log_json: bool = False

    # --- Server ---
    host: str = "0.0.0.0"  # noqa: S104 — bind-all is required inside containers
    port: int = 8000
    workers: int = 1

    # --- Database ---
    # Runtime FastAPI uses asyncpg. See `.env.example` for the format.
    database_url: PostgresDsn
    # Alembic uses psycopg (sync). MUST be set whenever the driver-specific
    # query string differs (e.g. Neon uses `sslmode=require` for psycopg and
    # `ssl=require` for asyncpg). If left blank, a best-effort fallback
    # rewrites `database_url` — works for local Docker Postgres without SSL.
    alembic_database_url: PostgresDsn | None = None
    database_echo: bool = False
    database_pool_size: int = 5
    database_pool_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_pool_recycle: int = 1800

    # --- Clerk ---
    clerk_issuer: AnyHttpUrl
    clerk_jwks_url: AnyHttpUrl
    clerk_audience: str | None = None
    clerk_authorized_parties: CsvStr
    clerk_webhook_secret: str = "whsec_replace_me"
    clerk_jwks_cache_ttl_seconds: int = 600

    # --- Razorpay (placeholder until Cycle 2) ---
    razorpay_key_id: str = "rzp_test_replace_me"
    razorpay_key_secret: str = "replace_me"
    razorpay_webhook_secret: str = "replace_me"

    # --- Cloudinary (placeholder until Cycle 1) ---
    cloudinary_cloud_name: str = "oorvashee"
    cloudinary_api_key: str = "replace_me"
    cloudinary_api_secret: str = "replace_me"
    cloudinary_upload_folder: str = "local/products"

    # --- Resend (placeholder until Cycle 2) ---
    resend_api_key: str = "re_replace_me"
    resend_from_email: str = "orders@oorvashee.local"

    # --- Bot webhook (optional, Cycle 6+) ---
    bot_webhook_token: str = ""
    whatsapp_app_secret: str = ""
    instagram_app_secret: str = ""

    # --- Observability ---
    sentry_dsn: str = ""

    # ---------- validators ----------

    @field_validator(
        "allowed_origins",
        "clerk_authorized_parties",
        mode="before",
    )
    @classmethod
    def _parse_csv(cls, value: str | list[str] | None) -> list[str]:
        return _split_csv(value)

    @field_validator("database_url", mode="before")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """Force the asyncpg driver — guards against accidentally passing a sync DSN."""
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("alembic_database_url", mode="before")
    @classmethod
    def _normalize_alembic_url(cls, value: str | None) -> str | None:
        """Coerce bare `postgresql://` to the psycopg driver.

        Empty string → None (lets the property fall back to `database_url`).
        Does NOT rewrite an asyncpg-style URL: those carry `?ssl=…` which
        psycopg rejects, so failing loudly here is better than silently
        producing a broken connection at first migrate.
        """
        if value is None or value == "":
            return None
        if isinstance(value, str) and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    # ---------- derived helpers ----------

    @property
    def is_prod(self) -> bool:
        return self.app_env is AppEnv.PROD

    @property
    def is_local(self) -> bool:
        return self.app_env is AppEnv.LOCAL

    @property
    def database_url_str(self) -> str:
        """SQLAlchemy wants a plain string, not a Pydantic URL."""
        return str(self.database_url)

    @property
    def database_url_sync(self) -> str:
        """Fallback sync URL derived from `database_url`.

        ONLY safe when the runtime URL has no driver-specific query params
        (e.g. local Docker Postgres without SSL). For Neon / any managed
        Postgres requiring SSL, set `alembic_database_url` explicitly —
        asyncpg's `?ssl=require` is not accepted by psycopg.
        """
        return self.database_url_str.replace("+asyncpg", "+psycopg")

    @property
    def effective_alembic_url(self) -> str:
        """The URL Alembic actually uses for migrations.

        Resolution order:
          1. `ALEMBIC_DATABASE_URL` env var (preferred — set on Railway/Neon).
          2. Fallback: `DATABASE_URL` with the driver swapped to psycopg.
             Only correct when the URL carries no driver-specific params.
        """
        if self.alembic_database_url is not None:
            return str(self.alembic_database_url)
        return self.database_url_sync


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Inject via `Depends(get_settings)`."""
    return Settings()  # type: ignore[call-arg]  # pydantic-settings reads env
