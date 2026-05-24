"""Application settings — loaded once at boot from environment.

All config access goes through `get_settings()`. Never read os.environ directly
in application code.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PROD = "prod"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


CsvStr = Annotated[list[str], Field(default_factory=list)]


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
    database_url: PostgresDsn
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
        """Alembic uses a sync driver for migration ops."""
        return self.database_url_str.replace("+asyncpg", "+psycopg")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Inject via `Depends(get_settings)`."""
    return Settings()  # type: ignore[call-arg]  # pydantic-settings reads env
