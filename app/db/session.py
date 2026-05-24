"""Async SQLAlchemy engine + session factory.

The engine is built once at app startup (see `app/main.py` lifespan) and
disposed at shutdown. Sessions are scoped per-request via `get_db()` in
`app/db/deps.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)


def build_engine(settings: Settings) -> AsyncEngine:
    """Construct the async engine. Called once at startup."""
    engine = create_async_engine(
        settings.database_url_str,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_recycle=settings.database_pool_recycle,
        pool_pre_ping=True,
        future=True,
    )
    log.info(
        "db_engine_built",
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
    )
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # NB: no `autocommit` kwarg — removed in SQLAlchemy 2.x. Passing it
    # propagates to AsyncSession.__init__ and raises TypeError on session
    # creation, which was the cause of the "DB unreachable" false negative.
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# App-state accessors (populated by the lifespan in app/main.py)
# ---------------------------------------------------------------------------


class _DbState:
    """Holds the live engine + sessionmaker. Mutated by lifespan."""

    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


db_state = _DbState()


def set_db_state(engine: AsyncEngine, sm: async_sessionmaker[AsyncSession]) -> None:
    db_state.engine = engine
    db_state.sessionmaker = sm


async def dispose_engine() -> None:
    if db_state.engine is not None:
        await db_state.engine.dispose()
        log.info("db_engine_disposed")
    db_state.engine = None
    db_state.sessionmaker = None


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the live sessionmaker.

    Used by `get_db()` (request scope) and may be reused by background tasks.
    Commits on success, rolls back on exception, always closes.
    """
    if db_state.sessionmaker is None:
        raise RuntimeError("DB sessionmaker not initialized. Did app startup run?")

    session = db_state.sessionmaker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
