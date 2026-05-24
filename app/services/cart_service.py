"""CartService — server-side cart for authenticated users.

Responsibilities:
- Validate variant existence + product publishability before any add/update.
- Soft-check inventory availability at add-time (NOT a lock; checkout
  re-validates under SELECT ... FOR UPDATE).
- Compute totals from snapshot prices.
- Prevent duplicate cart lines by upserting on (cart_id, variant_id).
- Hydrate response with everything the frontend needs (no N+1).

Guest carts live in localStorage and are submitted via /cart/merge after
sign-in. Anonymous flows never touch this service.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.core.exceptions import (
    NotFoundError,
    OutOfStockError,
    ProductUnavailableError,
    ValidationError,
)
from app.models.cart import Cart, CartItem
from app.models.enums import ProductStatus
from app.models.product_variant import ProductVariant
from app.repositories.cart_repo import CartRepository
from app.schemas.cart import (
    CartItemRead,
    CartRead,
    MergeCartLine,
)
from app.services.base import BaseService

# Cart caps — guards against pathological clients. Per-line cap is enforced
# at the Pydantic level (le=99); per-cart caps live here.
MAX_DISTINCT_ITEMS_PER_CART = 50


class CartService(BaseService):
    @property
    def repo(self) -> CartRepository:
        return CartRepository(self.session)

    # ---------- Read ----------

    async def get_cart(self, user_id: uuid.UUID) -> CartRead:
        cart = await self.repo.get_for_user(user_id)
        if cart is None:
            return _empty_cart()
        items = await self.repo.list_items_hydrated(cart.id)
        return self._serialize(cart, items)

    # ---------- Write ----------

    async def add_item(
        self, user_id: uuid.UUID, variant_id: uuid.UUID, quantity: int
    ) -> CartRead:
        self._guard_quantity(quantity)
        variant = await self._require_buyable_variant(variant_id)
        cart = await self.repo.get_or_create_for_user(user_id)

        existing = await self.repo.get_item_by_variant(cart.id, variant_id)
        target_qty = (existing.quantity if existing else 0) + quantity

        self._guard_stock(variant, target_qty)

        if existing is None:
            await self._guard_cart_capacity(cart.id)
            self.session.add(
                CartItem(cart_id=cart.id, variant_id=variant_id, quantity=quantity)
            )
        else:
            existing.quantity = target_qty
        await self.session.flush()

        items = await self.repo.list_items_hydrated(cart.id)
        self.log.info(
            "cart_item_added",
            user_id=str(user_id),
            variant_id=str(variant_id),
            quantity=quantity,
            new_line_quantity=target_qty,
        )
        return self._serialize(cart, items)

    async def update_item(
        self, user_id: uuid.UUID, item_id: uuid.UUID, quantity: int
    ) -> CartRead:
        self._guard_quantity(quantity)
        cart = await self.repo.get_for_user(user_id)
        if cart is None:
            raise NotFoundError("Cart is empty")

        item = await self.repo.get_item(cart.id, item_id)
        if item is None:
            raise NotFoundError("Cart item not found")

        # Variant validity may have changed since add (admin archived product, etc.)
        if item.variant.product.status not in (ProductStatus.PUBLISHED,):
            raise ProductUnavailableError(
                "This item is no longer available for purchase"
            )
        self._guard_stock(item.variant, quantity)

        item.quantity = quantity
        await self.session.flush()

        items = await self.repo.list_items_hydrated(cart.id)
        self.log.info(
            "cart_item_updated",
            user_id=str(user_id),
            item_id=str(item_id),
            quantity=quantity,
        )
        return self._serialize(cart, items)

    async def remove_item(
        self, user_id: uuid.UUID, item_id: uuid.UUID
    ) -> CartRead:
        cart = await self.repo.get_for_user(user_id)
        if cart is None:
            return _empty_cart()

        deleted = await self.repo.delete_item(cart.id, item_id)
        if deleted == 0:
            raise NotFoundError("Cart item not found")

        items = await self.repo.list_items_hydrated(cart.id)
        self.log.info(
            "cart_item_removed", user_id=str(user_id), item_id=str(item_id)
        )
        return self._serialize(cart, items)

    async def clear(self, user_id: uuid.UUID) -> CartRead:
        cart = await self.repo.get_for_user(user_id)
        if cart is None:
            return _empty_cart()
        await self.repo.clear(cart.id)
        self.log.info("cart_cleared", user_id=str(user_id))
        return self._serialize(cart, [])

    async def merge(
        self, user_id: uuid.UUID, lines: list[MergeCartLine]
    ) -> CartRead:
        """Idempotently merge a guest cart payload into the user's cart.

        Order matters per-line — duplicates inside `lines` are summed
        before the variant-existence check.
        """
        if not lines:
            return await self.get_cart(user_id)

        # Sum duplicates within the incoming payload.
        merged: dict[uuid.UUID, int] = {}
        for line in lines:
            merged[line.variant_id] = merged.get(line.variant_id, 0) + line.quantity

        cart = await self.repo.get_or_create_for_user(user_id)
        for variant_id, qty in merged.items():
            try:
                variant = await self._require_buyable_variant(variant_id)
            except (NotFoundError, ProductUnavailableError):
                # Skip lines whose product was archived since the guest
                # added it — don't fail the whole merge.
                self.log.warning(
                    "cart_merge_skipped_unavailable",
                    user_id=str(user_id),
                    variant_id=str(variant_id),
                )
                continue

            existing = await self.repo.get_item_by_variant(cart.id, variant_id)
            target_qty = (existing.quantity if existing else 0) + qty
            # Cap silently at available stock — merge should never fail noisily.
            available = self._available_stock(variant)
            target_qty = min(target_qty, available, 99)
            if target_qty == 0:
                continue
            if existing is None:
                self.session.add(
                    CartItem(
                        cart_id=cart.id, variant_id=variant_id, quantity=target_qty
                    )
                )
            else:
                existing.quantity = target_qty
        await self.session.flush()

        items = await self.repo.list_items_hydrated(cart.id)
        self.log.info(
            "cart_merged", user_id=str(user_id), incoming_lines=len(lines)
        )
        return self._serialize(cart, items)

    # ---------- Internal: validation ----------

    @staticmethod
    def _guard_quantity(quantity: int) -> None:
        if quantity < 1 or quantity > 99:
            raise ValidationError("quantity must be between 1 and 99")

    async def _require_buyable_variant(
        self, variant_id: uuid.UUID
    ) -> ProductVariant:
        variant = await self.repo.fetch_variant_for_cart(variant_id)
        if variant is None or not variant.is_active:
            raise NotFoundError("Variant not found")
        product = variant.product
        if product.status != ProductStatus.PUBLISHED:
            raise ProductUnavailableError(
                "Product is not currently available for purchase",
                extra={"product_status": product.status.value},
            )
        return variant

    @staticmethod
    def _available_stock(variant: ProductVariant) -> int:
        inv = variant.inventory
        if inv is None:
            return 0
        return max(inv.stock - inv.reserved, 0)

    @classmethod
    def _guard_stock(cls, variant: ProductVariant, requested_qty: int) -> None:
        available = cls._available_stock(variant)
        if requested_qty > available:
            raise OutOfStockError(
                f"Only {available} unit(s) available",
                extra={"available": available, "requested": requested_qty},
            )

    async def _guard_cart_capacity(self, cart_id: uuid.UUID) -> None:
        from sqlalchemy import func, select

        from app.models.cart import CartItem

        result = await self.session.execute(
            select(func.count()).select_from(CartItem).where(CartItem.cart_id == cart_id)
        )
        count = int(result.scalar_one())
        if count >= MAX_DISTINCT_ITEMS_PER_CART:
            raise ValidationError(
                f"Cart cannot exceed {MAX_DISTINCT_ITEMS_PER_CART} distinct items"
            )

    # ---------- Internal: serialization ----------

    @classmethod
    def _serialize(cls, cart: Cart, items) -> CartRead:  # type: ignore[no-untyped-def]
        rows = [cls._serialize_item(it) for it in items]
        subtotal = sum((r.line_total for r in rows), Decimal("0"))
        item_count = sum(r.quantity for r in rows)
        has_unavailable = any(not r.available for r in rows)
        return CartRead(
            id=cart.id,
            items=rows,
            item_count=item_count,
            distinct_count=len(rows),
            subtotal=subtotal,
            updated_at=cart.updated_at,
            has_unavailable_items=has_unavailable,
        )

    @classmethod
    def _serialize_item(cls, item: CartItem) -> CartItemRead:
        variant = item.variant
        product = variant.product
        unit_price = variant.price_override or product.base_price
        image_url = None
        for img in product.images:
            if img.is_primary:
                image_url = img.url
                break
        if image_url is None and product.images:
            image_url = product.images[0].url

        available_stock = cls._available_stock(variant)
        available = (
            product.status == ProductStatus.PUBLISHED
            and variant.is_active
            and available_stock >= item.quantity
        )
        variant_label_parts = [p for p in (variant.color, variant.fabric) if p]
        return CartItemRead(
            id=item.id,
            variant_id=variant.id,
            product_id=product.id,
            product_slug=product.slug,
            product_name=product.name,
            variant_label=" · ".join(variant_label_parts) if variant_label_parts else None,
            image_url=image_url,
            unit_price=unit_price,
            quantity=item.quantity,
            line_total=unit_price * item.quantity,
            available=available,
            max_quantity=available_stock,
        )


def _empty_cart() -> CartRead:
    return CartRead(
        id=None,
        items=[],
        item_count=0,
        distinct_count=0,
        subtotal=Decimal("0"),
        updated_at=None,
        has_unavailable_items=False,
    )
