"""Product data access — catalog reads."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from app.models.category import Category, ProductCategory
from app.models.enums import ProductStatus
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    model = Product

    # ---------- Public catalog reads ----------

    def _published_filter(self, stmt: Select) -> Select:
        return stmt.where(
            Product.status.in_(
                [ProductStatus.PUBLISHED, ProductStatus.UNAVAILABLE]
            )
        )

    def base_catalog_query(self) -> Select:
        """Query that returns only catalog-visible products.

        UNAVAILABLE rows are included so the frontend can render
        out-of-stock badges; the row is hidden by Cycle-1+ UI filters when
        a `?available=true` flag is passed.
        """
        return self._published_filter(select(Product))

    async def list_catalog(
        self,
        *,
        q: str | None = None,
        category_slugs: list[str] | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        sort: str = "new",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[Sequence[Product], int]:
        stmt = self.base_catalog_query()

        # Text search via the generated TSVECTOR column.
        if q:
            stmt = stmt.where(
                Product.search_vector.op("@@")(func.plainto_tsquery("english", q))
            )

        # Categories: join through the junction; multi-category = OR semantics.
        if category_slugs:
            stmt = (
                stmt.join(ProductCategory, ProductCategory.product_id == Product.id)
                .join(Category, Category.id == ProductCategory.category_id)
                .where(Category.slug.in_(category_slugs))
                .distinct()
            )

        if min_price is not None:
            stmt = stmt.where(Product.base_price >= min_price)
        if max_price is not None:
            stmt = stmt.where(Product.base_price <= max_price)

        match sort:
            case "price_asc":
                stmt = stmt.order_by(Product.base_price.asc(), Product.id.asc())
            case "price_desc":
                stmt = stmt.order_by(Product.base_price.desc(), Product.id.desc())
            case "bestseller":
                stmt = stmt.order_by(
                    Product.is_bestseller.desc(),
                    Product.published_at.desc().nullslast(),
                )
            case "featured":
                stmt = stmt.order_by(
                    Product.featured.desc(),
                    Product.published_at.desc().nullslast(),
                )
            case _:  # "new" (default)
                stmt = stmt.order_by(
                    Product.published_at.desc().nullslast(),
                    Product.created_at.desc(),
                )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self.session.execute(count_stmt)).scalar_one())

        result = await self.session.execute(
            stmt.options(selectinload(Product.images)).limit(limit).offset(offset)
        )
        return result.scalars().unique().all(), total

    async def get_by_slug_full(self, slug: str) -> Product | None:
        """Full product detail, including variants + inventory + images + categories.

        Includes ARCHIVED rows so `/products/{slug}` can return 200 with
        `available=false` and preserve the bot URL contract (PRD §7.2).
        """
        stmt = (
            select(Product)
            .where(Product.slug == slug)
            .options(
                selectinload(Product.images),
                selectinload(Product.variants).selectinload(ProductVariant.inventory),
                selectinload(Product.category_links).selectinload(ProductCategory.category),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_total_stock(self, product_id: uuid.UUID) -> int:
        stmt = (
            select(func.coalesce(func.sum(Inventory.stock), 0))
            .select_from(Inventory)
            .join(ProductVariant, ProductVariant.id == Inventory.variant_id)
            .where(
                ProductVariant.product_id == product_id,
                ProductVariant.is_active.is_(True),
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())
