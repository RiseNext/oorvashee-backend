"""Request-scoped DB dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a session for the lifetime of the request.

    Commit on success, rollback on exception. Services should NOT call
    `commit()` themselves under normal flow; explicit `async with session.begin():`
    is allowed for nested savepoints.
    """
    async for session in session_scope():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]
