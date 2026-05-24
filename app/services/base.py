"""BaseService — shared scaffolding for domain services.

A service owns:
  - business rules + validation that depends on DB state
  - orchestration across multiple repositories
  - transactional boundaries via `async with session.begin():`
  - side-effects (queue email, call Razorpay, etc.)

Routers depend on services, never repositories. Repositories never commit.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger


class BaseService:
    """Common dependencies + logging. Subclasses add domain methods."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.log = get_logger(self.__class__.__module__)
