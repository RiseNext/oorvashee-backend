"""WishlistService."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.exceptions import NotFoundError, ValidationError
from app.models.enums import ProductStatus
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.wishlist import Wishlist
from app.repositories.wishlist_repo import WishlistRepository
from app.schemas.cart import CartRead
from app.schemas.wishlist import WishlistItemRead, WishlistRead
from app.services.base import BaseService
from app.services.cart_service import CartService


class WishlistService(BaseService):
    @property
    def repo(self) -> WishlistRepository:
        return WishlistRepository(self.session)

    async def list_for_user(self, user_id: uuid.UUID) -> WishlistRead:
        rows = await self.repo.list_for_user(user_id)
        items = [self._serialize(row) for row in rows]
        return WishlistRead(items=items, count=len(items))

    async def add(self, user_id: uuid.UUID, product_id: uuid.UUID) -> WishlistRead:
        product = await self.session.get(Product, product_id)
        if product is None or product.status == ProductStatus.ARCHIVED:
            raise NotFoundError("Product not found")

        existing = await self.repo.get(user_id, product_id)
        if existing is None:
            self.session.add(Wishlist(user_id=user_id, product_id=product_id))
            await self.session.flush()
            self.log.info(
                "wishlist_added",
                user_id=str(user_id),
                product_id=str(product_id),
            )
        return await self.list_for_user(user_id)

    async def remove(self, user_id: uuid.UUID, product_id: uuid.UUID) -> WishlistRead:
        await self.repo.delete(user_id, product_id)
        # Idempotent: never errors on missing row.
        self.log.info(
            "wishlist_removed", user_id=str(user_id), product_id=str(product_id)
        )
        return await self.list_for_user(user_id)

    async def move_to_cart(
        self,
        user_id: uuid.UUID,
        product_id: uuid.UUID,
        variant_id: uuid.UUID,
        quantity: int,
        remove_from_wishlist: bool,
    ) -> CartRead:
        """Add (product, variant) to cart; optionally drop from wishlist.

        Validates that the variant actually belongs to the wishlisted
        product — defends against tampered requests.
        """
        stmt = select(ProductVariant).where(
            ProductVariant.id == variant_id,
            ProductVariant.product_id == product_id,
        )
        variant = (await self.session.execute(stmt)).scalar_one_or_none()
        if variant is None:
            raise ValidationError("Variant does not belong to this product")

        cart_read = await CartService(self.session).add_item(
            user_id, variant_id, quantity
        )
        if remove_from_wishlist:
            await self.repo.delete(user_id, product_id)
        self.log.info(
            "wishlist_moved_to_cart",
            user_id=str(user_id),
            product_id=str(product_id),
            variant_id=str(variant_id),
            quantity=quantity,
        )
        return cart_read

    # ---------- Serialization ----------

    @staticmethod
    def _serialize(row: Wishlist) -> WishlistItemRead:
        product = row.product
        primary_url = None
        for img in product.images:
            if img.is_primary:
                primary_url = img.url
                break
        if primary_url is None and product.images:
            primary_url = product.images[0].url
        return WishlistItemRead(
            id=row.id,
            product_id=product.id,
            product_slug=product.slug,
            product_name=product.name,
            base_price=product.base_price,
            mrp=product.mrp,
            primary_image_url=primary_url,
            available=product.status == ProductStatus.PUBLISHED,
            added_at=row.added_at,
        )
