"""Alembic environment.

Reads the DB URL from app settings (single source of truth), runs migrations
synchronously via psycopg (asyncpg isn't supported by Alembic's run loop).
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make `app/` importable when Alembic runs from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.models import Base

# Alembic Config object — picks up alembic.ini's [alembic] section.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the URL from settings so we don't keep two sources of truth.
# `effective_alembic_url` prefers ALEMBIC_DATABASE_URL (psycopg, with
# `sslmode=require` for Neon) and falls back to DATABASE_URL with the
# driver swapped — only safe for local Postgres without SSL.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.effective_alembic_url)

# Target metadata for autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection — emit SQL to stdout."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
