"""Sanity tests for settings loading + DSN normalisation."""

from __future__ import annotations

import importlib

from app.core import config as config_module


def _fresh_settings() -> config_module.Settings:
    """Force a new Settings instance bypassing the lru_cache."""
    importlib.reload(config_module)
    return config_module.get_settings()


def test_settings_loads_from_env(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://a.test, http://b.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/d")
    # setenv("") overrides .env; validator coerces "" → None.
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "")

    settings = _fresh_settings()
    assert settings.app_env.value == "staging"
    assert settings.allowed_origins == ["http://a.test", "http://b.test"]
    assert "+asyncpg" in str(settings.database_url)
    assert "+psycopg" in settings.database_url_sync


def test_settings_csv_handles_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CLERK_AUTHORIZED_PARTIES", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/d")
    settings = _fresh_settings()
    assert settings.clerk_authorized_parties == []


def test_effective_alembic_url_falls_back_to_database_url(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Local-dev mode: only DATABASE_URL set, no SSL params — fallback works."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/d")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "")  # override .env to "unset"

    settings = _fresh_settings()
    assert settings.alembic_database_url is None
    assert settings.effective_alembic_url == "postgresql+psycopg://u:p@localhost:5432/d"


def test_effective_alembic_url_uses_explicit_value(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Neon mode: BOTH URLs set, each with its driver-native SSL param."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:p@host-pooler.eu.aws.neon.tech/d?ssl=require",
    )
    monkeypatch.setenv(
        "ALEMBIC_DATABASE_URL",
        "postgresql+psycopg://u:p@host-pooler.eu.aws.neon.tech/d?sslmode=require",
    )
    settings = _fresh_settings()
    url = settings.effective_alembic_url
    assert "+psycopg" in url
    assert "sslmode=require" in url
    assert "ssl=require" not in url  # the asyncpg-style param is NOT leaked through


def test_alembic_url_empty_string_treated_as_unset(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Empty `ALEMBIC_DATABASE_URL` must fall back, not crash on URL parsing."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "")
    settings = _fresh_settings()
    assert settings.alembic_database_url is None
    assert "+psycopg" in settings.effective_alembic_url


def test_alembic_url_bare_postgresql_gets_psycopg_driver(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`postgresql://...` (no driver) auto-upgrades to `postgresql+psycopg://...`."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql://u:p@h/d?sslmode=require")
    settings = _fresh_settings()
    assert "+psycopg" in str(settings.alembic_database_url)
