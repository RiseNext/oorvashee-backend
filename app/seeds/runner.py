"""Seed orchestration: run / reset / status.

All public functions are async + take an `AsyncSession`. They do NOT commit —
the caller (CLI script) controls the transaction so a partial failure rolls
back cleanly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.banner import Banner
from app.models.category import Category, ProductCategory
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.product_image import ProductImage
from app.models.product_variant import ProductVariant
from app.seeds.base import (
    SeedReport,
    get_or_create,
    picsum_url,
    seed_public_id,
)
from app.seeds.data import (
    BANNERS,
    CATEGORIES,
    DEFAULT_PRODUCT_STATUS,
    LEGACY_CATEGORY_SLUGS,
    LEGACY_PRODUCT_SLUGS,
    PRODUCTS,
)

# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


async def _seed_categories(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    for data in CATEGORIES:
        # Mutable display/SEO fields refreshed on every run; `kind` is set on
        # create only (changing a category's kind is an admin/migration concern).
        fields = {
            "name": data["name"],
            "display_order": data["display_order"],
            "description": data.get("description"),
            "seo_title": data.get("seo_title"),
            "seo_description": data.get("seo_description"),
            "image_url": data.get("image_url"),
        }
        _, created, updated = await get_or_create(
            session,
            Category,
            lookup={"slug": data["slug"]},
            defaults={**fields, "kind": data["kind"], "is_active": True},
            update_fields=fields,
        )
        if created:
            report.created["categories"] += 1
        elif updated:
            report.updated["categories"] += 1
        else:
            report.skipped["categories"] += 1

    # Second pass: resolve `parent_slug` → `parent_id` now that every category
    # row exists. Self-referential hierarchy ("Pattu" → weave children).
    await _link_category_parents(session, report)
    return report


async def _link_category_parents(
    session: AsyncSession, report: SeedReport
) -> None:
    slugs = [c["slug"] for c in CATEGORIES]
    rows = (
        await session.execute(select(Category).where(Category.slug.in_(slugs)))
    ).scalars().all()
    by_slug = {c.slug: c for c in rows}
    for data in CATEGORIES:
        parent_slug = data.get("parent_slug")
        if not parent_slug:
            continue
        child = by_slug.get(data["slug"])
        parent = by_slug.get(parent_slug)
        if child is None or parent is None:
            continue
        if child.parent_id != parent.id:
            child.parent_id = parent.id
            report.updated["category_parents"] += 1
        else:
            report.skipped["category_parents"] += 1
    await session.flush()


# ---------------------------------------------------------------------------
# Products + Variants + Inventory + Images + Category links
# ---------------------------------------------------------------------------


async def _resolve_category_ids(
    session: AsyncSession, slugs: list[str]
) -> list[Category]:
    if not slugs:
        return []
    stmt = select(Category).where(Category.slug.in_(slugs))
    return list((await session.execute(stmt)).scalars().all())


async def _seed_one_product(
    session: AsyncSession, data: dict[str, Any], report: SeedReport
) -> None:
    """Idempotently seed one product + all its dependents."""
    product, created, updated = await get_or_create(
        session,
        Product,
        lookup={"slug": data["slug"]},
        defaults={
            "name": data["name"],
            "short_description": data["short_description"],
            "description": data["description"],
            "base_price": data["base_price"],
            "mrp": data["mrp"],
            "tags": data["tags"],
            "featured": data["featured"],
            "is_bestseller": data["is_bestseller"],
            "is_new": data["is_new"],
            "status": DEFAULT_PRODUCT_STATUS,
        },
        update_fields={
            "name": data["name"],
            "short_description": data["short_description"],
            "description": data["description"],
            "base_price": data["base_price"],
            "mrp": data["mrp"],
            "tags": data["tags"],
            "featured": data["featured"],
            "is_bestseller": data["is_bestseller"],
            "is_new": data["is_new"],
        },
    )
    if created:
        report.created["products"] += 1
    elif updated:
        report.updated["products"] += 1
    else:
        report.skipped["products"] += 1

    # --- Category links ---------------------------------------------------
    categories = await _resolve_category_ids(session, data["categories"])
    existing_links = {
        link.category_id
        for link in (
            await session.execute(
                select(ProductCategory).where(ProductCategory.product_id == product.id)
            )
        ).scalars()
    }
    for cat in categories:
        if cat.id not in existing_links:
            session.add(ProductCategory(product_id=product.id, category_id=cat.id))
            report.created["product_categories"] += 1
        else:
            report.skipped["product_categories"] += 1
    await session.flush()

    # --- Variants + Inventory --------------------------------------------
    for v in data["variants"]:
        variant, v_created, v_updated = await get_or_create(
            session,
            ProductVariant,
            lookup={"sku": v["sku"]},
            defaults={
                "product_id": product.id,
                "color": v["color"],
                "fabric": v["fabric"],
                "is_default": v["is_default"],
                "is_active": True,
            },
            update_fields={
                "color": v["color"],
                "fabric": v["fabric"],
                "is_default": v["is_default"],
            },
        )
        if v_created:
            report.created["variants"] += 1
        elif v_updated:
            report.updated["variants"] += 1
        else:
            report.skipped["variants"] += 1

        _, inv_created, inv_updated = await get_or_create(
            session,
            Inventory,
            lookup={"variant_id": variant.id},
            defaults={
                "stock": v["stock"],
                "reserved": 0,
                "low_stock_threshold": 2,
            },
            update_fields={"stock": v["stock"]},
        )
        if inv_created:
            report.created["inventory"] += 1
        elif inv_updated:
            report.updated["inventory"] += 1
        else:
            report.skipped["inventory"] += 1

    # --- Images -----------------------------------------------------------
    # Position 0 = primary. Skip if already populated for this product.
    image_count_stmt = select(func.count()).select_from(ProductImage).where(
        ProductImage.product_id == product.id
    )
    existing_images = int((await session.execute(image_count_stmt)).scalar_one())
    if existing_images == 0:
        for idx in range(data["image_count"]):
            session.add(
                ProductImage(
                    product_id=product.id,
                    cloudinary_public_id=seed_public_id(data["slug"], idx),
                    url=picsum_url(f"oorvashee-{data['slug']}-{idx}"),
                    alt_text=f"{data['name']} — view {idx + 1}",
                    position=idx,
                    is_primary=(idx == 0),
                    width=800,
                    height=1000,
                )
            )
            report.created["product_images"] += 1
        await session.flush()
    else:
        report.skipped["product_images"] += existing_images


async def _seed_products(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    for data in PRODUCTS:
        await _seed_one_product(session, data, report)
    return report


# ---------------------------------------------------------------------------
# Banners
# ---------------------------------------------------------------------------


async def _seed_banners(session: AsyncSession) -> SeedReport:
    report = SeedReport()
    for data in BANNERS:
        category = None
        if data["category_slug"]:
            category = (
                await session.execute(
                    select(Category).where(Category.slug == data["category_slug"])
                )
            ).scalar_one_or_none()

        image_url = (
            picsum_url(data["image_seed"], width=1600, height=720)
            if data["image_seed"]
            else None
        )

        _, created, updated = await get_or_create(
            session,
            Banner,
            lookup={"title": data["title"], "placement": data["placement"]},
            defaults={
                "subtitle": data["subtitle"],
                "image_url": image_url,
                "cta_label": data["cta_label"],
                "cta_url": data["cta_url"],
                "category_id": category.id if category else None,
                "display_order": data["display_order"],
                "is_active": True,
            },
            update_fields={
                "subtitle": data["subtitle"],
                "image_url": image_url,
                "cta_label": data["cta_label"],
                "cta_url": data["cta_url"],
                "category_id": category.id if category else None,
                "display_order": data["display_order"],
            },
        )
        if created:
            report.created["banners"] += 1
        elif updated:
            report.updated["banners"] += 1
        else:
            report.skipped["banners"] += 1
    return report


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_seeds(session: AsyncSession) -> SeedReport:
    """Apply categories → products → banners. Idempotent on natural keys."""
    overall = SeedReport()
    overall.merge(await _seed_categories(session))
    overall.merge(await _seed_products(session))
    overall.merge(await _seed_banners(session))
    return overall


async def reset_seeds(session: AsyncSession) -> SeedReport:
    """Delete every row this seed system owns.

    Order matters — banners reference categories; products cascade to
    variants/inventory/images/category-links; categories are last because
    nothing seed-owned references them by then.
    """
    report = SeedReport()

    # --- Banners (lookup by title + placement) ----------------------------
    for data in BANNERS:
        result = await session.execute(
            delete(Banner).where(
                Banner.title == data["title"],
                Banner.placement == data["placement"],
            )
        )
        report.created["banners"] -= 0  # initialise key for printout
        report.updated["banners"] += result.rowcount or 0

    # --- Products (cascade handles variants/inventory/images/category links)
    # Include LEGACY_PRODUCT_SLUGS so the pre-alignment demo catalog is removed
    # from Neon on reseed, leaving no orphans.
    slugs = [p["slug"] for p in PRODUCTS] + LEGACY_PRODUCT_SLUGS
    result = await session.execute(delete(Product).where(Product.slug.in_(slugs)))
    report.updated["products"] += result.rowcount or 0

    # --- Categories -------------------------------------------------------
    # Include LEGACY_CATEGORY_SLUGS so the old fabric taxonomy is deleted too.
    cat_slugs = [c["slug"] for c in CATEGORIES] + LEGACY_CATEGORY_SLUGS
    result = await session.execute(delete(Category).where(Category.slug.in_(cat_slugs)))
    report.updated["categories"] += result.rowcount or 0

    return report


async def seed_status(session: AsyncSession) -> dict[str, int]:
    """Counts of seed-owned rows currently in the DB."""
    cat_slugs = [c["slug"] for c in CATEGORIES]
    product_slugs = [p["slug"] for p in PRODUCTS]
    variant_skus = [v["sku"] for p in PRODUCTS for v in p["variants"]]

    async def _count(stmt: Any) -> int:
        return int((await session.execute(stmt)).scalar_one())

    return {
        "categories": await _count(
            select(func.count()).select_from(Category).where(Category.slug.in_(cat_slugs))
        ),
        "products": await _count(
            select(func.count()).select_from(Product).where(Product.slug.in_(product_slugs))
        ),
        "variants": await _count(
            select(func.count()).select_from(ProductVariant).where(ProductVariant.sku.in_(variant_skus))
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
        "banners": await _count(
            select(func.count())
            .select_from(Banner)
            .where(Banner.title.in_([b["title"] for b in BANNERS]))
        ),
    }
