"""Production-safe merchandising sync.

Reconciles the canonical catalog (categories + products + variants + inventory
+ images + banners, declared in `app.seeds.data`) into whatever database it is
pointed at — **without any destructive operation against referenced data.**

Guarantees (this module contains NO `DELETE`/`TRUNCATE`/`DROP`):
- Categories are UPSERTed by slug; products by slug; variants by SKU. IDs are
  never reassigned, so historical `order_items.product_id` / `variant_id`
  references stay valid forever.
- Retired products become `status=archived` (hidden from the catalog list but
  still resolvable by slug for the bot URL contract); they are never deleted.
- Retired categories become `is_active=false`; they are never deleted, so any
  product↔category links from archived/historical products stay intact.
- Inventory is reconciled for canonical variants only; archived products'
  inventory is left untouched for order-history accounting.
- Image creation is best-effort and idempotent (skipped when images exist).

The operation is idempotent: re-running makes no changes once the DB matches
the canonical set. The caller owns the transaction (commit/rollback).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.banner import Banner
from app.models.category import Category, ProductCategory
from app.models.enums import ProductStatus
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
    PRODUCTS,
    RETIRED_CATEGORY_SLUGS,
    RETIRED_PRODUCT_SLUGS,
)


class MerchandisingSync:
    """Idempotent, non-destructive catalog reconciliation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def sync(self) -> SeedReport:
        report = SeedReport()
        await self._upsert_categories(report)
        await self._link_category_parents(report)
        await self._deactivate_retired_categories(report)
        for data in PRODUCTS:
            await self._upsert_product(data, report)
        await self._archive_retired_products(report)
        await self._upsert_banners(report)
        return report

    # ----------------------------------------------------------------- categories

    async def _upsert_categories(self, report: SeedReport) -> None:
        for data in CATEGORIES:
            # `kind` set on create only; mutable display/SEO fields refreshed.
            fields = {
                "name": data["name"],
                "display_order": data["display_order"],
                "description": data.get("description"),
                "seo_title": data.get("seo_title"),
                "seo_description": data.get("seo_description"),
                "image_url": data.get("image_url"),
            }
            # A previously-retired category that is now canonical again must be
            # reactivated — include is_active in the refresh.
            fields_with_active = {**fields, "is_active": True}
            _, created, updated = await get_or_create(
                self.session,
                Category,
                lookup={"slug": data["slug"]},
                defaults={**fields_with_active, "kind": data["kind"]},
                update_fields=fields_with_active,
            )
            self._tally(report, "categories", created, updated)

    async def _link_category_parents(self, report: SeedReport) -> None:
        slugs = [c["slug"] for c in CATEGORIES]
        rows = (
            await self.session.execute(select(Category).where(Category.slug.in_(slugs)))
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
        await self.session.flush()

    async def _deactivate_retired_categories(self, report: SeedReport) -> None:
        """Soft-retire categories no longer in the canonical set. NEVER deletes —
        product↔category links from historical products must remain intact."""
        if not RETIRED_CATEGORY_SLUGS:
            return
        rows = (
            await self.session.execute(
                select(Category).where(Category.slug.in_(RETIRED_CATEGORY_SLUGS))
            )
        ).scalars().all()
        for cat in rows:
            if cat.is_active:
                cat.is_active = False
                report.updated["categories_deactivated"] += 1
            else:
                report.skipped["categories_deactivated"] += 1
        await self.session.flush()

    # ------------------------------------------------------------------ products

    async def _resolve_categories(self, slugs: list[str]) -> list[Category]:
        if not slugs:
            return []
        stmt = select(Category).where(Category.slug.in_(slugs))
        return list((await self.session.execute(stmt)).scalars().all())

    async def _upsert_product(self, data: dict[str, Any], report: SeedReport) -> None:
        """Idempotently upsert one canonical product + its dependents.

        A product that was previously retired but is canonical again is
        re-published here (status reset to the canonical default)."""
        product_fields = {
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
        }
        product, created, updated = await get_or_create(
            self.session,
            Product,
            lookup={"slug": data["slug"]},
            defaults=product_fields,
            update_fields=product_fields,
        )
        self._tally(report, "products", created, updated)

        # --- Category links (additive — never prune; pruning could remove an
        # admin-applied link, and links carry no order-history meaning) -------
        categories = await self._resolve_categories(data["categories"])
        existing_links = {
            link.category_id
            for link in (
                await self.session.execute(
                    select(ProductCategory).where(
                        ProductCategory.product_id == product.id
                    )
                )
            ).scalars()
        }
        for cat in categories:
            if cat.id not in existing_links:
                self.session.add(
                    ProductCategory(product_id=product.id, category_id=cat.id)
                )
                report.created["product_categories"] += 1
            else:
                report.skipped["product_categories"] += 1
        await self.session.flush()

        # --- Variants (upsert by SKU — stable ids) + inventory reconcile -----
        for v in data["variants"]:
            variant_fields = {
                "color": v["color"],
                "fabric": v["fabric"],
                "is_default": v["is_default"],
                "is_active": True,
            }
            variant, v_created, v_updated = await get_or_create(
                self.session,
                ProductVariant,
                lookup={"sku": v["sku"]},
                defaults={**variant_fields, "product_id": product.id},
                update_fields=variant_fields,
            )
            self._tally(report, "variants", v_created, v_updated)

            _, inv_created, inv_updated = await get_or_create(
                self.session,
                Inventory,
                lookup={"variant_id": variant.id},
                defaults={
                    "stock": v["stock"],
                    "reserved": 0,
                    "low_stock_threshold": 2,
                },
                # Reconcile stock only; never touch `reserved` (live cart/order
                # holds). This is safe — it adjusts on-hand, not commitments.
                update_fields={"stock": v["stock"]},
            )
            self._tally(report, "inventory", inv_created, inv_updated)

        # --- Images (best-effort, idempotent — create only when none exist) --
        existing_images = int(
            (
                await self.session.execute(
                    select(func.count())
                    .select_from(ProductImage)
                    .where(ProductImage.product_id == product.id)
                )
            ).scalar_one()
        )
        if existing_images == 0:
            for idx in range(data["image_count"]):
                self.session.add(
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
            await self.session.flush()
        else:
            report.skipped["product_images"] += existing_images

    async def _archive_retired_products(self, report: SeedReport) -> None:
        """Archive products dropped from the canonical catalog. NEVER deletes —
        these rows may be referenced by `order_items`. Archiving hides them from
        the catalog list while keeping the slug resolvable (bot URL contract)
        and the row + id stable for order history."""
        if not RETIRED_PRODUCT_SLUGS:
            return
        rows = (
            await self.session.execute(
                select(Product).where(Product.slug.in_(RETIRED_PRODUCT_SLUGS))
            )
        ).scalars().all()
        for product in rows:
            if product.status is not ProductStatus.ARCHIVED:
                product.status = ProductStatus.ARCHIVED
                product.featured = False
                product.is_bestseller = False
                product.is_new = False
                report.updated["products_archived"] += 1
            else:
                report.skipped["products_archived"] += 1
        await self.session.flush()

    # ------------------------------------------------------------------- banners

    async def _upsert_banners(self, report: SeedReport) -> None:
        for data in BANNERS:
            category = None
            if data["category_slug"]:
                category = (
                    await self.session.execute(
                        select(Category).where(Category.slug == data["category_slug"])
                    )
                ).scalar_one_or_none()

            image_url = (
                picsum_url(data["image_seed"], width=1600, height=720)
                if data["image_seed"]
                else None
            )
            banner_fields = {
                "subtitle": data["subtitle"],
                "image_url": image_url,
                "cta_label": data["cta_label"],
                "cta_url": data["cta_url"],
                "category_id": category.id if category else None,
                "display_order": data["display_order"],
                "is_active": True,
            }
            _, created, updated = await get_or_create(
                self.session,
                Banner,
                lookup={"title": data["title"], "placement": data["placement"]},
                defaults=banner_fields,
                update_fields=banner_fields,
            )
            self._tally(report, "banners", created, updated)

    # -------------------------------------------------------------------- helper

    @staticmethod
    def _tally(report: SeedReport, key: str, created: bool, updated: bool) -> None:
        if created:
            report.created[key] += 1
        elif updated:
            report.updated[key] += 1
        else:
            report.skipped[key] += 1
