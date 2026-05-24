"""Cart data access. No business rules — services own those."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.repositories.base import BaseRepository


class CartRepository(BaseRepository[Cart]):
    model = Cart

    async def get_for_user(self, user_id: uuid.UUID) -> Cart | None:
        stmt = select(Cart).where(Cart.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create_for_user(self, user_id: uuid.UUID) -> Cart:
        cart = await self.get_for_user(user_id)
        if cart is not None:
            return cart
        cart = Cart(user_id=user_id)
        self.session.add(cart)
        await self.session.flush()
        return cart

    async def list_items_hydrated(self, cart_id: uuid.UUID) -> Sequence[CartItem]:
        """Cart items with variant + product + primary image preloaded.

        One round trip via `selectinload` — no N+1.
        """
        stmt = (
            select(CartItem)
            .where(CartItem.cart_id == cart_id)
            .options(
                selectinload(CartItem.variant)
                .selectinload(ProductVariant.inventory),
                selectinload(CartItem.variant)
                .selectinload(ProductVariant.product)
                .selectinload(Product.images),
            )
            .order_by(CartItem.added_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_item(self, cart_id: uuid.UUID, item_id: uuid.UUID) -> CartItem | None:
        stmt = (
            select(CartItem)
            .where(CartItem.id == item_id, CartItem.cart_id == cart_id)
            .options(
                selectinload(CartItem.variant)
                .selectinload(ProductVariant.inventory),
                selectinload(CartItem.variant)
                .selectinload(ProductVariant.product)
                .selectinload(Product.images),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_item_by_variant(
        self, cart_id: uuid.UUID, variant_id: uuid.UUID
    ) -> CartItem | None:
        stmt = select(CartItem).where(
            CartItem.cart_id == cart_id, CartItem.variant_id == variant_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def delete_item(self, cart_id: uuid.UUID, item_id: uuid.UUID) -> int:
        stmt = delete(CartItem).where(
            CartItem.id == item_id, CartItem.cart_id == cart_id
        )
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def clear(self, cart_id: uuid.UUID) -> int:
        stmt = delete(CartItem).where(CartItem.cart_id == cart_id)
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def fetch_variant_for_cart(
        self, variant_id: uuid.UUID
    ) -> ProductVariant | None:
        """Hydrated variant — used by the service to validate add-to-cart."""
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.id == variant_id)
            .options(
                selectinload(ProductVariant.inventory),
                selectinload(ProductVariant.product).selectinload(Product.images),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
