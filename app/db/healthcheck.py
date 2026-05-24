"""DB-level health probe used by `/health`."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.db.session import db_state

log = get_logger(__name__)


async def db_ping() -> bool:
    """Return True if the DB responds to `SELECT 1`.

    Never raises — but always logs the underlying cause when it returns False,
    so failure modes (TypeError on session construction, asyncpg connection
    error, SSL handshake failure, Neon pooler timeout, etc.) are diagnosable
    from the logs instead of silently surfacing as "unreachable".
    """
    if db_state.sessionmaker is None:
        log.error(
            "db_ping_skipped",
            reason="sessionmaker not initialised — lifespan did not run",
        )
        return False

    try:
        async with db_state.sessionmaker() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar() == 1
    except SQLAlchemyError as exc:
        log.exception(
            "db_ping_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            category="sqlalchemy",
        )
        return False
    except Exception as exc:
        # Catches connection-driver errors (asyncpg.*), TypeError from bad
        # session construction, anything else upstream of SQLAlchemy.
        log.exception(
            "db_ping_failed",
            error_type=type(exc).__name__,
            error_message=str(exc),
            category="non_sqlalchemy",
        )
        return False
