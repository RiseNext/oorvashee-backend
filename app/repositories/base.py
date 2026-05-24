"""Generic async repository base.

Cycle 1+ repositories inherit and add domain-specific queries. The base only
covers the predictable CRUD primitives so that 80% of repository code is
write-once.

Convention:
    - Repositories NEVER commit. The calling service controls the transaction.
    - Repositories accept and return ORM model instances, never Pydantic schemas.
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, TypeVar

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """CRUD primitives over a single ORM model.

    Subclasses set `model = MyModel` and add methods like `find_by_slug`,
    `list_published`, etc.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self.session.get(self.model, entity_id)

    async def list(self, *, limit: int = 50, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()  # populate server defaults (id, timestamps)
        await self.session.refresh(entity)
        return entity

    async def update(self, entity: ModelT, **changes: Any) -> ModelT:
        for key, value in changes.items():
            setattr(entity, key, value)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        await self.session.delete(entity)
        await self.session.flush()

    async def delete_by_id(self, entity_id: uuid.UUID) -> int:
        """Delete without loading the row. Returns affected count."""
        stmt = delete(self.model).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return result.rowcount or 0
