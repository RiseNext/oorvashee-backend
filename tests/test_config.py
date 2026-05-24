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
