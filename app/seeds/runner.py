"""Seed orchestration — thin wrappers over the production-safe MerchandisingSync.

`run_seeds` applies the canonical catalog idempotently and **non-destructively**
(see `app/seeds/sync.py`). There is intentionally **no destructive reset** any
more: retiring products/categories is done by archiving / deactivation inside
the sync, so order history and any order-referenced rows are preserved forever.

The caller owns the transaction (commit/rollback).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.enums import ProductStatus
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.product_image import ProductImage
from app.models.product_variant import ProductVariant
from app.seeds.base import SeedReport
from app.seeds.data import (
    CATEGORIES,
    PRODUCTS,
    RETIRED_CATEGORY_SLUGS,
    RETIRED_PRODUCT_SLUGS,
)
from app.seeds.sync import MerchandisingSync


async def run_seeds(session: AsyncSession) -> SeedReport:
    """Apply the canonical catalog idempotently + non-destructively.

    Upserts categories/products/variants/inventory/images/banners, archives
    retired products, and deactivates retired categories. Safe to re-run; safe
    against a production DB with live order history.
    """
    return await MerchandisingSync(session).sync()


async def seed_status(session: AsyncSession) -> dict[str, int]:
    """Counts that confirm a sync landed correctly (verification aid)."""
    cat_slugs = [c["slug"] for c in CATEGORIES]
    product_slugs = [p["slug"] for p in PRODUCTS]
    variant_skus = [v["sku"] for p in PRODUCTS for v in p["variants"]]

    async def _count(stmt) -> int:  # type: ignore[no-untyped-def]
        return int((await session.execute(stmt)).scalar_one())

    return {
        "categories_canonical": await _count(
            select(func.count()).select_from(Category).where(Category.slug.in_(cat_slugs))
        ),
        "categories_active": await _count(
            select(func.count())
            .select_from(Category)
            .where(Category.slug.in_(cat_slugs), Category.is_active.is_(True))
        ),
        "products_canonical": await _count(
            select(func.count()).select_from(Product).where(Product.slug.in_(product_slugs))
        ),
        "products_published": await _count(
            select(func.count())
            .select_from(Product)
            .where(
                Product.slug.in_(product_slugs),
                Product.status == ProductStatus.PUBLISHED,
            )
        ),
        "variants": await _count(
            select(func.count())
            .select_from(ProductVariant)
            .where(ProductVariant.sku.in_(variant_skus))
        ),
        "inventory_rows": await _count(
            select(func.count())
            .select_from(Inventory)
            .join(ProductVariant, ProductVariant.id == Inventory.variant_id)
            .where(ProductVariant.sku.in_(variant_skus))
        ),
        "product_images": await _count(
            select(func.count())
            .select_from(ProductImage)
            .join(Product, Product.id == ProductImage.product_id)
            .where(Product.slug.in_(product_slugs))
        ),
        # Retired set — proves safe retirement (archived, not deleted).
        "retired_products_present": await _count(
            select(func.count())
            .select_from(Product)
            .where(Product.slug.in_(RETIRED_PRODUCT_SLUGS))
        ),
        "retired_products_archived": await _count(
            select(func.count())
            .select_from(Product)
            .where(
                Product.slug.in_(RETIRED_PRODUCT_SLUGS),
                Product.status == ProductStatus.ARCHIVED,
            )
        ),
        "retired_categories_inactive": await _count(
            select(func.count())
            .select_from(Category)
            .where(
                Category.slug.in_(RETIRED_CATEGORY_SLUGS),
                Category.is_active.is_(False),
            )
        ),
    }
