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

        # Text search: full-text on the generated TSVECTOR (name / short / long
        # description / tags) OR a case-insensitive substring match on the
        # admin-managed product code (e.g. "SKS2-1950"). `code` is NOT part of
        # `search_vector`, and hyphenated alphanumeric codes don't tokenise
        # reliably under FTS, so ILIKE covers full + partial + case-insensitive
        # code lookups. Mirrors the slug-ILIKE pattern in `admin_list_query`.
        # `code` is nullable → a NULL row yields NULL (non-match), never an error.
        if q:
            stmt = stmt.where(
                Product.search_vector.op("@@")(func.plainto_tsquery("english", q))
                | Product.code.ilike(f"%{q}%")
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
        `available=false` and preserve the bot URL contract (PRD §7.2). DRAFT
        (never-published) rows are EXCLUDED so unreleased products never leak to
        anonymous callers — the public slug endpoint 404s on them.
        """
        stmt = (
            select(Product)
            .where(Product.slug == slug, Product.status != ProductStatus.DRAFT)
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

    # ---------- Admin reads -------------------------------------------------

    async def slug_exists(self, slug: str) -> bool:
        """UNIQUE check used by the service before INSERT for a clean 409."""
        stmt = select(func.count()).select_from(Product).where(Product.slug == slug)
        return int((await self.session.execute(stmt)).scalar_one()) > 0

    async def get_admin(self, product_id: uuid.UUID) -> Product | None:
        """Full eager-loaded fetch for admin views.

        Includes variants + inventory + images + category links so the
        admin detail endpoint hits the DB exactly once.
        """
        stmt = (
            select(Product)
            .where(Product.id == product_id)
            .options(
                selectinload(Product.images),
                selectinload(Product.variants).selectinload(ProductVariant.inventory),
                selectinload(Product.category_links),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def admin_list_query(
        self,
        *,
        q: str | None = None,
        statuses: list[ProductStatus] | None = None,
        category_slug: str | None = None,
        featured_only: bool = False,
    ) -> Select:
        """Build the admin list base query.

        Distinct from `base_catalog_query` — admin sees DRAFT + ARCHIVED
        too. Sorting + pagination are applied by the service.
        """
        stmt = select(Product)

        if statuses:
            stmt = stmt.where(Product.status.in_(statuses))

        if q:
            # FTS covers most cases; ILIKE on slug catches admins searching
            # by URL-fragment which the FTS index doesn't tokenise on.
            stmt = stmt.where(
                Product.search_vector.op("@@")(func.plainto_tsquery("english", q))
                | Product.slug.ilike(f"%{q.lower()}%")
            )

        if category_slug:
            stmt = (
                stmt.join(ProductCategory, ProductCategory.product_id == Product.id)
                .join(Category, Category.id == ProductCategory.category_id)
                .where(Category.slug == category_slug)
                .distinct()
            )

        if featured_only:
            stmt = stmt.where(Product.featured.is_(True))

        return stmt.order_by(Product.updated_at.desc(), Product.id.desc())

    async def admin_aggregate_stock(
        self, product_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """Total stock + variant count keyed by product id — one round-trip.

        Returns: `{product_id: (total_stock, variant_count)}`. Used by the
        admin list endpoint to avoid an N+1 across the page.
        """
        if not product_ids:
            return {}
        stmt = (
            select(
                ProductVariant.product_id,
                func.coalesce(func.sum(Inventory.stock), 0),
                func.count(ProductVariant.id.distinct()),
            )
            .select_from(ProductVariant)
            .outerjoin(Inventory, Inventory.variant_id == ProductVariant.id)
            .where(ProductVariant.product_id.in_(product_ids))
            .group_by(ProductVariant.product_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: (int(row[1]), int(row[2])) for row in rows}
