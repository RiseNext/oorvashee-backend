"""Wishlist data access."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.models.product import Product
from app.models.wishlist import Wishlist
from app.repositories.base import BaseRepository


class WishlistRepository(BaseRepository[Wishlist]):
    model = Wishlist

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Wishlist]:
        stmt = (
            select(Wishlist)
            .where(Wishlist.user_id == user_id)
            .options(selectinload(Wishlist.product).selectinload(Product.images))
            .order_by(Wishlist.added_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get(
        self, user_id: uuid.UUID, product_id: uuid.UUID
    ) -> Wishlist | None:
        stmt = select(Wishlist).where(
            Wishlist.user_id == user_id, Wishlist.product_id == product_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete(self, user_id: uuid.UUID, product_id: uuid.UUID) -> int:
        stmt = delete(Wishlist).where(
            Wishlist.user_id == user_id, Wishlist.product_id == product_id
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0
