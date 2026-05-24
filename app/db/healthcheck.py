"""DB-level health probe used by `/health`."""

from __future__ import annotations

from sqlalchemy import text

from app.db.session import db_state


async def db_ping() -> bool:
    """Return True if the DB responds to `SELECT 1`. Never raises."""
    if db_state.sessionmaker is None:
        return False
    try:
        async with db_state.sessionmaker() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception:
        return False
