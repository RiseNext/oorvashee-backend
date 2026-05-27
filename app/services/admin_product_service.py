"""AdminProductService — admin-facing product CRUD + lifecycle + audit.

This service is the SINGLE place where admin product mutations happen.
The router never touches the ORM directly; it parses + delegates.

Key invariants enforced here:
- Slug is immutable after creation. The Update schema doesn't expose it;
  this service additionally refuses to set it.
- Status transitions go through dedicated methods (publish / unpublish /
  archive / unarchive). UNAVAILABLE is a system state — never settable by
  admin directly. The InventoryService writes UNAVAILABLE when total stock
  reaches zero.
- Every mutation writes one `AuditLog` row with action + entity + summary
  + before/after metadata. No PII in metadata (see audit_log.py docstring).
- Category replacement is delete-then-insert inside the request transaction
  (atomic). Variants are added/updated explicitly via dedicated methods.

Concurrency notes:
- Slug uniqueness is DB-enforced (UNIQUE constraint). We pre-check for a
  clean 409 but the constraint is the source of truth.
- We never UPDATE `search_vector` directly — the BEFORE INSERT/UPDATE
  trigger (see migration 0002) re-derives it whenever name / description /
  short_description / tags change.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.pagination import OffsetParams, Page, paginate_offset
from app.models.category import Category, ProductCategory
from app.models.enums import AuditAction, ProductStatus
from app.models.inventory import Inventory
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.repositories.product_repo import ProductRepository
from app.schemas.admin_product import (
    AdminCategoryAssignRequest,
    AdminImageRead,
    AdminProductCreate,
    AdminProductListItem,
    AdminProductRead,
    AdminProductUpdate,
    AdminVariantCreate,
    AdminVariantRead,
    AdminVariantUpdate,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService

log = get_logger(__name__)


# Statuses an admin may TARGET via the lifecycle endpoints. UNAVAILABLE is
# system-managed; admins use archive/unarchive instead.
_ADMIN_LIFECYCLE_TARGETS = {
    ProductStatus.DRAFT,
    ProductStatus.PUBLISHED,
    ProductStatus.ARCHIVED,
}


def _slugify(name: str) -> str:
    """URL-safe slug from a product name.

    Strips accents-naively (relies on input being ASCII-friendly per PRD
    English-only scope §14). Trims to 220 chars to fit the column.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return cleaned[:220] or "product"


class AdminProductService(BaseService):
    @property
    def products(self) -> ProductRepository:
        return ProductRepository(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ======================================================================
    # Reads
    # ======================================================================

    async def list_products(
        self,
        *,
        params: OffsetParams,
        q: str | None,
        statuses: list[ProductStatus] | None,
        category_slug: str | None,
        featured_only: bool,
    ) -> Page[AdminProductListItem]:
        stmt = self.products.admin_list_query(
            q=q,
            statuses=statuses,
            category_slug=category_slug,
            featured_only=featured_only,
        )
        rows, total = await paginate_offset(self.session, stmt, params)
        product_rows: list[Product] = list(rows)

        # Single round-trip aggregation: total stock + variant count per
        # product id. Avoids an N+1 across the page.
        stock_map = await self.products.admin_aggregate_stock(
            [p.id for p in product_rows]
        )

        # Primary image URL per product — one targeted query, not per-row.
        primary_image_urls: dict[uuid.UUID, str] = {}
        if product_rows:
            from app.models.product_image import ProductImage

            img_stmt = select(ProductImage.product_id, ProductImage.url).where(
                ProductImage.product_id.in_([p.id for p in product_rows]),
                ProductImage.is_primary.is_(True),
            )
            for pid, url in (await self.session.execute(img_stmt)).all():
                primary_image_urls[pid] = url

        items = [
            AdminProductListItem(
                id=p.id,
                slug=p.slug,
                name=p.name,
                status=p.status,
                base_price=p.base_price,
                currency=p.currency,
                featured=p.featured,
                is_bestseller=p.is_bestseller,
                is_new=p.is_new,
                primary_image_url=primary_image_urls.get(p.id),
                total_stock=stock_map.get(p.id, (0, 0))[0],
                variant_count=stock_map.get(p.id, (0, 0))[1],
                published_at=p.published_at,
                updated_at=p.updated_at,
            )
            for p in product_rows
        ]
        return Page[AdminProductListItem](items=items, total=total)

    async def get_product(self, product_id: uuid.UUID) -> AdminProductRead:
        product = await self.products.get_admin(product_id)
        if product is None:
            raise NotFoundError("Product not found")
        return self._to_read(product)

    # ======================================================================
    # Create
    # ======================================================================

    async def create_product(
        self,
        body: AdminProductCreate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        slug = await self._resolve_unique_slug(body.slug or _slugify(body.name))

        # Categories: pre-validate all ids exist + are active.
        category_rows = await self._fetch_categories(body.category_ids)
        # Variants: pre-check SKU uniqueness across the batch.
        self._assert_unique_skus_in_payload(body.variants)

        product = Product(
            slug=slug,
            name=body.name.strip(),
            short_description=body.short_description,
            description=body.description,
            base_price=body.base_price,
            mrp=body.mrp,
            currency=body.currency,
            status=ProductStatus.DRAFT,
            tags=body.tags,
            featured=body.featured,
            is_bestseller=body.is_bestseller,
            is_new=body.is_new,
            seo_title=body.seo_title,
            seo_description=body.seo_description,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        self.session.add(product)
        try:
            await self.session.flush()  # surface UNIQUE collisions early
        except IntegrityError as exc:  # pragma: no cover — pre-check usually catches
            raise ConflictError(
                "Product slug or SKU collision",
                code="product_unique_violation",
            ) from exc

        # Category links.
        for cat in category_rows:
            self.session.add(
                ProductCategory(product_id=product.id, category_id=cat.id)
            )

        # Variants + inventory.
        for variant_payload in body.variants:
            await self._create_variant(
                product_id=product.id, payload=variant_payload
            )

        await self.session.flush()

        await self.audit.record(
            action=AuditAction.CREATE,
            entity_type="product",
            entity_id=product.id,
            actor_user_id=actor_user_id,
            summary=f"Created product '{product.name}'",
            metadata={
                "slug": product.slug,
                "variant_count": len(body.variants),
                "category_count": len(category_rows),
            },
            request_id=request_id,
        )

        log.info(
            "admin_product_created",
            product_id=str(product.id),
            slug=product.slug,
            actor=str(actor_user_id),
        )

        # Reload with eager joins so the response is hydrated.
        return self._to_read(await self._reload(product.id))

    # ======================================================================
    # Update
    # ======================================================================

    async def update_product(
        self,
        product_id: uuid.UUID,
        body: AdminProductUpdate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        product = await self._require_product(product_id)
        before = self._snapshot(product)

        # `model_dump(exclude_unset=True)` honours PATCH semantics —
        # fields the client didn't send stay untouched.
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise ValidationError("Update body has no fields to apply")

        for field, value in changes.items():
            setattr(product, field, value)

        if "name" in changes:
            product.name = changes["name"].strip()

        product.updated_by = actor_user_id
        await self.session.flush()

        after = self._snapshot(product)
        diff = {
            k: {"from": before[k], "to": after[k]}
            for k in changes
            if before.get(k) != after.get(k)
        }

        if diff:
            await self.audit.record(
                action=AuditAction.UPDATE,
                entity_type="product",
                entity_id=product.id,
                actor_user_id=actor_user_id,
                summary=f"Updated product '{product.name}' fields: {sorted(diff)}",
                metadata={"changed": diff},
                request_id=request_id,
            )

        return self._to_read(await self._reload(product.id))

    # ======================================================================
    # Lifecycle transitions
    # ======================================================================

    async def publish(
        self,
        product_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        product = await self._require_product(product_id)
        self._assert_transition_allowed(product.status, ProductStatus.PUBLISHED)

        # Refuse to publish if there are no active variants — would create
        # a product page the customer can never buy from.
        if not await self._has_active_variants(product_id):
            raise ValidationError(
                "Cannot publish a product with no active variants",
                code="product_has_no_variants",
            )

        before = product.status
        product.status = ProductStatus.PUBLISHED
        product.updated_by = actor_user_id
        # Only set `published_at` the FIRST time it's published — preserves
        # the "first goes live" timestamp for sort_by=new across re-publishes.
        if product.published_at is None:
            product.published_at = datetime.now(UTC)

        await self.session.flush()
        await self._record_status_change(
            product, before, actor_user_id, request_id, summary_verb="published"
        )
        return self._to_read(await self._reload(product.id))

    async def unpublish(
        self,
        product_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        product = await self._require_product(product_id)
        self._assert_transition_allowed(product.status, ProductStatus.DRAFT)
        before = product.status
        product.status = ProductStatus.DRAFT
        product.updated_by = actor_user_id
        await self.session.flush()
        await self._record_status_change(
            product, before, actor_user_id, request_id, summary_verb="unpublished"
        )
        return self._to_read(await self._reload(product.id))

    async def archive(
        self,
        product_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        product = await self._require_product(product_id)
        self._assert_transition_allowed(product.status, ProductStatus.ARCHIVED)
        before = product.status
        product.status = ProductStatus.ARCHIVED
        product.updated_by = actor_user_id
        await self.session.flush()
        await self._record_status_change(
            product, before, actor_user_id, request_id, summary_verb="archived"
        )
        return self._to_read(await self._reload(product.id))

    async def unarchive(
        self,
        product_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        product = await self._require_product(product_id)
        if product.status is not ProductStatus.ARCHIVED:
            raise ValidationError(
                "Only archived products can be unarchived",
                code="invalid_status_transition",
            )
        before = product.status
        # Unarchive always returns to DRAFT — admin can re-publish if intended.
        product.status = ProductStatus.DRAFT
        product.updated_by = actor_user_id
        await self.session.flush()
        await self._record_status_change(
            product, before, actor_user_id, request_id, summary_verb="unarchived"
        )
        return self._to_read(await self._reload(product.id))

    # ======================================================================
    # Categories
    # ======================================================================

    async def set_categories(
        self,
        product_id: uuid.UUID,
        body: AdminCategoryAssignRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminProductRead:
        product = await self._require_product(product_id)

        target = await self._fetch_categories(body.category_ids)
        target_ids = {c.id for c in target}

        existing_links = (
            await self.session.execute(
                select(ProductCategory).where(ProductCategory.product_id == product.id)
            )
        ).scalars().all()
        existing_ids = {link.category_id for link in existing_links}

        to_add = target_ids - existing_ids
        to_remove = existing_ids - target_ids

        if to_remove:
            await self.session.execute(
                delete(ProductCategory).where(
                    ProductCategory.product_id == product.id,
                    ProductCategory.category_id.in_(to_remove),
                )
            )
        for cat_id in to_add:
            self.session.add(
                ProductCategory(product_id=product.id, category_id=cat_id)
            )
        await self.session.flush()

        product.updated_by = actor_user_id
        await self.session.flush()

        if to_add or to_remove:
            await self.audit.record(
                action=AuditAction.UPDATE,
                entity_type="product",
                entity_id=product.id,
                actor_user_id=actor_user_id,
                summary=f"Set categories on '{product.name}'",
                metadata={
                    "added": [str(c) for c in to_add],
                    "removed": [str(c) for c in to_remove],
                    "final_count": len(target_ids),
                },
                request_id=request_id,
            )

        return self._to_read(await self._reload(product.id))

    # ======================================================================
    # Variants
    # ======================================================================

    async def add_variant(
        self,
        product_id: uuid.UUID,
        payload: AdminVariantCreate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminVariantRead:
        product = await self._require_product(product_id)
        if payload.is_default:
            await self._clear_default_on_other_variants(product.id)
        variant = await self._create_variant(product_id=product.id, payload=payload)
        product.updated_by = actor_user_id
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.CREATE,
            entity_type="product_variant",
            entity_id=variant.id,
            actor_user_id=actor_user_id,
            summary=f"Added variant {variant.sku} to '{product.name}'",
            metadata={
                "product_id": str(product.id),
                "sku": variant.sku,
                "initial_stock": payload.initial_stock,
            },
            request_id=request_id,
        )
        return await self._variant_read(variant)

    async def update_variant(
        self,
        product_id: uuid.UUID,
        variant_id: uuid.UUID,
        payload: AdminVariantUpdate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminVariantRead:
        variant = await self._require_variant(product_id, variant_id)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise ValidationError("Update body has no fields to apply")

        # If promoting to default, clear current default first.
        if changes.get("is_default") is True and not variant.is_default:
            await self._clear_default_on_other_variants(product_id)

        before = {k: getattr(variant, k) for k in changes}
        for k, v in changes.items():
            setattr(variant, k, v)
        await self.session.flush()
        after = {k: getattr(variant, k) for k in changes}
        diff = {k: {"from": before[k], "to": after[k]} for k in changes if before[k] != after[k]}

        if diff:
            await self.audit.record(
                action=AuditAction.UPDATE,
                entity_type="product_variant",
                entity_id=variant.id,
                actor_user_id=actor_user_id,
                summary=f"Updated variant {variant.sku}",
                metadata={"changed": diff, "product_id": str(product_id)},
                request_id=request_id,
            )

        return await self._variant_read(variant)

    async def deactivate_variant(
        self,
        product_id: uuid.UUID,
        variant_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        """Soft-delete: `is_active = false`.

        Hard delete is dangerous — `order_items` may FK-reference the
        variant. Deactivation removes it from the catalog while preserving
        order history.
        """
        variant = await self._require_variant(product_id, variant_id)
        if not variant.is_active:
            # Idempotent — already inactive.
            return
        variant.is_active = False
        if variant.is_default:
            variant.is_default = False
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="product_variant",
            entity_id=variant.id,
            actor_user_id=actor_user_id,
            summary=f"Deactivated variant {variant.sku}",
            metadata={"product_id": str(product_id), "is_active": False},
            request_id=request_id,
        )

    # ======================================================================
    # Internal helpers
    # ======================================================================

    async def _require_product(self, product_id: uuid.UUID) -> Product:
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found")
        return product

    async def _require_variant(
        self, product_id: uuid.UUID, variant_id: uuid.UUID
    ) -> ProductVariant:
        stmt = select(ProductVariant).where(
            ProductVariant.id == variant_id,
            ProductVariant.product_id == product_id,
        )
        variant = (await self.session.execute(stmt)).scalar_one_or_none()
        if variant is None:
            raise NotFoundError("Variant not found on this product")
        return variant

    async def _reload(self, product_id: uuid.UUID) -> Product:
        product = await self.products.get_admin(product_id)
        if product is None:  # should never happen — caller just touched it
            raise NotFoundError("Product not found after write")
        return product

    async def _resolve_unique_slug(self, candidate: str) -> str:
        """Return the candidate if free, else append `-N` until it is.

        Best-effort race protection — the UNIQUE constraint is still the
        authoritative gate. Bound the loop to avoid pathological retries.
        """
        candidate = candidate.lower().strip("-")[:220]
        if not await self.products.slug_exists(candidate):
            return candidate
        for i in range(2, 100):
            attempt = f"{candidate[:215]}-{i}"
            if not await self.products.slug_exists(attempt):
                return attempt
        raise ConflictError(
            "Could not resolve a unique slug after 100 attempts",
            code="slug_collision_exhausted",
        )

    @staticmethod
    def _assert_unique_skus_in_payload(
        variants: list[AdminVariantCreate],
    ) -> None:
        skus = [v.sku for v in variants]
        if len(set(skus)) != len(skus):
            raise ValidationError(
                "Duplicate SKU in variant payload",
                code="duplicate_sku_in_payload",
            )

    async def _fetch_categories(
        self, category_ids: list[uuid.UUID]
    ) -> list[Category]:
        if not category_ids:
            return []
        stmt = select(Category).where(
            Category.id.in_(category_ids), Category.is_active.is_(True)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        if len(rows) != len(set(category_ids)):
            raise ValidationError(
                "One or more category IDs are invalid or inactive",
                code="invalid_category_ids",
                extra={
                    "requested": [str(c) for c in category_ids],
                    "found": [str(c.id) for c in rows],
                },
            )
        return rows

    async def _create_variant(
        self, *, product_id: uuid.UUID, payload: AdminVariantCreate
    ) -> ProductVariant:
        variant = ProductVariant(
            product_id=product_id,
            sku=payload.sku.strip(),
            color=payload.color,
            fabric=payload.fabric,
            size=payload.size,
            price_override=payload.price_override,
            weight_grams=payload.weight_grams,
            is_default=payload.is_default,
            is_active=payload.is_active,
        )
        self.session.add(variant)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"SKU '{payload.sku}' already exists",
                code="sku_already_exists",
            ) from exc

        # Seed inventory row so checkout's FOR UPDATE lock has a target.
        inventory = Inventory(
            variant_id=variant.id,
            stock=payload.initial_stock,
            reserved=0,
            low_stock_threshold=payload.low_stock_threshold,
        )
        self.session.add(inventory)
        await self.session.flush()

        return variant

    async def _clear_default_on_other_variants(self, product_id: uuid.UUID) -> None:
        """At most one default per product — service-level convention."""
        await self.session.execute(
            ProductVariant.__table__.update()
            .where(
                ProductVariant.product_id == product_id,
                ProductVariant.is_default.is_(True),
            )
            .values(is_default=False)
        )

    async def _has_active_variants(self, product_id: uuid.UUID) -> bool:
        stmt = (
            select(ProductVariant.id)
            .where(
                ProductVariant.product_id == product_id,
                ProductVariant.is_active.is_(True),
            )
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    @staticmethod
    def _assert_transition_allowed(
        current: ProductStatus, target: ProductStatus
    ) -> None:
        if target not in _ADMIN_LIFECYCLE_TARGETS:
            raise ValidationError(
                f"Admin cannot set status to '{target}'",
                code="invalid_status_target",
            )
        if current is target:
            raise ValidationError(
                f"Product is already '{target}'",
                code="status_unchanged",
            )
        # UNAVAILABLE → anything is allowed (admin can rescue a system-flagged
        # row by publishing or archiving). All other moves are free except
        # ARCHIVED → PUBLISHED which must go via unarchive (DRAFT) first to
        # force a deliberate two-step.
        if current is ProductStatus.ARCHIVED and target is ProductStatus.PUBLISHED:
            raise ValidationError(
                "Archived products must be unarchived (→draft) before publishing",
                code="invalid_status_transition",
            )

    @staticmethod
    def _snapshot(product: Product) -> dict[str, Any]:
        """Serialisable snapshot for audit-log diffs."""
        return {
            "name": product.name,
            "short_description": product.short_description,
            "description": product.description,
            "base_price": _to_str(product.base_price),
            "mrp": _to_str(product.mrp),
            "tags": list(product.tags),
            "featured": product.featured,
            "is_bestseller": product.is_bestseller,
            "is_new": product.is_new,
            "seo_title": product.seo_title,
            "seo_description": product.seo_description,
            "status": product.status.value,
        }

    async def _record_status_change(
        self,
        product: Product,
        before: ProductStatus,
        actor_user_id: uuid.UUID,
        request_id: str | None,
        *,
        summary_verb: str,
    ) -> None:
        await self.audit.record(
            action=AuditAction.STATUS_CHANGED,
            entity_type="product",
            entity_id=product.id,
            actor_user_id=actor_user_id,
            summary=f"Product '{product.name}' {summary_verb}",
            metadata={"from": before.value, "to": product.status.value},
            request_id=request_id,
        )

    # ---------- Response mapping --------------------------------------------

    @staticmethod
    async def _variant_read(variant: ProductVariant) -> AdminVariantRead:
        # Caller is expected to have loaded `variant.inventory` already, but
        # we defend against a None inventory just in case (defensive shape).
        inv = variant.inventory
        stock = inv.stock if inv else 0
        reserved = inv.reserved if inv else 0
        threshold = inv.low_stock_threshold if inv else 0
        return AdminVariantRead(
            id=variant.id,
            sku=variant.sku,
            color=variant.color,
            fabric=variant.fabric,
            size=variant.size,
            price_override=variant.price_override,
            weight_grams=variant.weight_grams,
            is_default=variant.is_default,
            is_active=variant.is_active,
            stock=stock,
            reserved=reserved,
            low_stock_threshold=threshold,
            created_at=variant.created_at,
            updated_at=variant.updated_at,
        )

    @staticmethod
    def _to_read(product: Product) -> AdminProductRead:
        variants_read = []
        for v in product.variants:
            inv = v.inventory
            variants_read.append(
                AdminVariantRead(
                    id=v.id,
                    sku=v.sku,
                    color=v.color,
                    fabric=v.fabric,
                    size=v.size,
                    price_override=v.price_override,
                    weight_grams=v.weight_grams,
                    is_default=v.is_default,
                    is_active=v.is_active,
                    stock=inv.stock if inv else 0,
                    reserved=inv.reserved if inv else 0,
                    low_stock_threshold=inv.low_stock_threshold if inv else 0,
                    created_at=v.created_at,
                    updated_at=v.updated_at,
                )
            )

        return AdminProductRead(
            id=product.id,
            slug=product.slug,
            name=product.name,
            short_description=product.short_description,
            description=product.description,
            base_price=product.base_price,
            mrp=product.mrp,
            currency=product.currency,
            status=product.status,
            tags=list(product.tags),
            featured=product.featured,
            is_bestseller=product.is_bestseller,
            is_new=product.is_new,
            seo_title=product.seo_title,
            seo_description=product.seo_description,
            published_at=product.published_at,
            created_at=product.created_at,
            updated_at=product.updated_at,
            created_by=product.created_by,
            updated_by=product.updated_by,
            variants=variants_read,
            images=[AdminImageRead.model_validate(img) for img in product.images],
            category_ids=[link.category_id for link in product.category_links],
        )


def _to_str(value: Decimal | None) -> str | None:
    """Decimal → str so the audit JSON is stable across clients."""
    return str(value) if value is not None else None
