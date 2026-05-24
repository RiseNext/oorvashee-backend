"""Category data access — read-only at this layer (admin writes land later)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select

from app.models.category import Category
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository[Category]):
    model = Category

    async def list_active(self) -> Sequence[Category]:
        stmt = (
            select(Category)
            .where(
                Category.deleted_at.is_(None),
                Category.is_active.is_(True),
            )
            .order_by(Category.kind, Category.display_order, Category.name)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_slug(self, slug: str) -> Category | None:
        stmt = select(Category).where(
            Category.slug == slug,
            Category.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
