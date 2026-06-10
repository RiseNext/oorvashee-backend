"""Inventory + StockMovement data access for admin views.

Stock-mutation paths live in `app/services/inventory_service.py` (the
single mutation owner). This repository is read-only — admin list, audit
timeline, health aggregates.

N+1 prevention: all admin list queries return JOIN-hydrated rows in one
round-trip via `selectinload` / scalar subqueries. Movement counts and
inventory aggregates use single-shot GROUP BY statements.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Integer, Select, cast, func, select
from sqlalchemy.orm import selectinload

from app.models.enums import StockMovementReason
from app.models.inventory import Inventory, StockMovement
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.repositories.base import BaseRepository


class InventoryRepository(BaseRepository[Inventory]):
    model = Inventory

    # ---------- Lookups -----------------------------------------------------

    async def get_for_variant(self, variant_id: uuid.UUID) -> Inventory | None:
        stmt = select(Inventory).where(Inventory.variant_id == variant_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_variant_and_product(
        self, variant_id: uuid.UUID
    ) -> Inventory | None:
        """Eager-load variant + product for admin detail views."""
        stmt = (
            select(Inventory)
            .where(Inventory.variant_id == variant_id)
            .options(
                selectinload(Inventory.variant).selectinload(ProductVariant.product)
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- Admin list --------------------------------------------------

    def admin_list_query(
        self,
        *,
        q: str | None = None,
        low_stock_only: bool = False,
        out_of_stock_only: bool = False,
        active_variants_only: bool = True,
    ) -> Select:
        """List query joining inventory ↔ variant ↔ product.

        - `low_stock_only`: stock > 0 AND stock <= threshold.
        - `out_of_stock_only`: stock = 0.
        - The two flags are mutually exclusive at the API layer; the
          repository accepts either.
        - `q` matches product name OR variant SKU (case-insensitive).
        - `active_variants_only` filters inactive variants (deactivated by
          AdminProductService). Defaults True — inactive variants shouldn't
          clutter the operational view.
        """
        stmt = (
            select(Inventory)
            .join(ProductVariant, ProductVariant.id == Inventory.variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .options(
                selectinload(Inventory.variant).selectinload(ProductVariant.product)
            )
        )
        if active_variants_only:
            stmt = stmt.where(ProductVariant.is_active.is_(True))
        if out_of_stock_only:
            stmt = stmt.where(Inventory.stock == 0)
        elif low_stock_only:
            stmt = stmt.where(
                Inventory.stock > 0,
                Inventory.stock <= Inventory.low_stock_threshold,
            )
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                ProductVariant.sku.ilike(like) | Product.name.ilike(like)
            )
        return stmt.order_by(Inventory.stock.asc(), ProductVariant.sku.asc())

    # ---------- Movements ---------------------------------------------------

    def movements_query(
        self,
        *,
        variant_id: uuid.UUID | None = None,
        reasons: list[StockMovementReason] | None = None,
        actor_user_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Select:
        """Movement audit timeline. Newest first.

        Joined with variant + product for the admin UI's "what changed"
        column. Pagination is applied by the caller via `paginate_offset`.
        """
        stmt = (
            select(StockMovement)
            .options(
                selectinload(StockMovement.variant).selectinload(ProductVariant.product)
            )
        )
        if variant_id is not None:
            stmt = stmt.where(StockMovement.variant_id == variant_id)
        if reasons:
            stmt = stmt.where(StockMovement.reason.in_(reasons))
        if actor_user_id is not None:
            stmt = stmt.where(StockMovement.actor_user_id == actor_user_id)
        if order_id is not None:
            stmt = stmt.where(StockMovement.order_id == order_id)
        if since is not None:
            stmt = stmt.where(StockMovement.created_at >= since)
        if until is not None:
            stmt = stmt.where(StockMovement.created_at <= until)
        return stmt.order_by(StockMovement.created_at.desc(), StockMovement.id.desc())

    async def recent_movements_for_variant(
        self, variant_id: uuid.UUID, *, limit: int = 20
    ) -> Sequence[StockMovement]:
        """Used by the variant-detail endpoint — fixed-size most-recent slice."""
        stmt = (
            select(StockMovement)
            .where(StockMovement.variant_id == variant_id)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    # ---------- Health aggregates -------------------------------------------

    async def health_aggregates(self) -> dict[str, int]:
        """Single round-trip aggregate over active variants.

        Returns:
          - `active_variants` — count of `is_active=true` variants with an
            inventory row.
          - `total_stock_units` — SUM(stock).
          - `total_reserved_units` — SUM(reserved).
          - `out_of_stock_variants` — count where stock = 0.
          - `low_stock_variants` — count where 0 < stock <= threshold.

        Stock VALUE (price-weighted) is intentionally NOT computed here —
        joining to product + handling variant.price_override fallback would
        bloat this single query, and value reporting is read by a different
        endpoint that can afford the join. Keep this query stable + fast.
        """
        stmt = (
            select(
                func.count(Inventory.id),
                func.coalesce(func.sum(Inventory.stock), 0),
                func.coalesce(func.sum(Inventory.sold_quantity), 0),
                func.coalesce(
                    func.sum(cast(Inventory.stock == 0, Integer)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        cast(
                            (Inventory.stock > 0)
                            & (Inventory.stock <= Inventory.low_stock_threshold),
                            Integer,
                        )
                    ),
                    0,
                ),
            )
            .select_from(Inventory)
            .join(ProductVariant, ProductVariant.id == Inventory.variant_id)
            .where(ProductVariant.is_active.is_(True))
        )
        row = (await self.session.execute(stmt)).one()
        return {
            "active_variants": int(row[0]),
            "total_stock_units": int(row[1]),
            "total_sold_units": int(row[2]),
            "out_of_stock_variants": int(row[3]),
            "low_stock_variants": int(row[4]),
        }
