"""AdminCategoryService — admin-facing category CRUD + hierarchy + audit.

Mirrors AdminProductService's patterns:
- Single mutation seam (router never touches the ORM).
- Slug is server-derived at create, IMMUTABLE thereafter (PRD §10 URL contract).
- `kind` is also immutable — switching axes (FABRIC → OCCASION) would break
  the catalog filter UI. Force admin to create a new category instead.
- `is_active` toggled via dedicated archive/unarchive endpoints (clean audit
  trail with `STATUS_CHANGED` action).
- Image swaps trigger best-effort Cloudinary destroy on the OLD public_id.
- Parent hierarchy: cycle prevention + depth cap to keep mega-menu sane.
- Every mutation writes one `audit_logs` row.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import (
    ConflictError,
    IntegrationError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.pagination import OffsetParams, Page, paginate_offset
from app.integrations.cloudinary_client import CloudinaryClient
from app.models.category import Category
from app.models.enums import AuditAction, CategoryKind
from app.repositories.category_repo import CategoryRepository
from app.schemas.admin_category import (
    AdminCategoryCreate,
    AdminCategoryListItem,
    AdminCategoryRead,
    AdminCategoryUpdate,
    CategoryReorderRequest,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService

log = get_logger(__name__)

# Limits the parent-chain depth so the future mega-menu UI doesn't have to
# render arbitrarily deep nesting. Bump if a real merchandising need lands.
MAX_DEPTH = 3


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return cleaned[:160] or "category"


class AdminCategoryService(BaseService):
    @property
    def categories(self) -> CategoryRepository:
        return CategoryRepository(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ======================================================================
    # Reads
    # ======================================================================

    async def list_categories(
        self,
        *,
        params: OffsetParams,
        q: str | None,
        kinds: list[CategoryKind] | None,
        is_active: bool | None,
        parent_id: uuid.UUID | None,
    ) -> Page[AdminCategoryListItem]:
        stmt = self.categories.admin_list_query(
            q=q, kinds=kinds, is_active=is_active, parent_id=parent_id,
        )
        rows, total = await paginate_offset(self.session, stmt, params)
        category_rows: list[Category] = list(rows)

        counts = await self.categories.admin_product_counts(
            [c.id for c in category_rows]
        )

        items = [
            AdminCategoryListItem(
                id=c.id,
                slug=c.slug,
                name=c.name,
                kind=c.kind,
                parent_id=c.parent_id,
                display_order=c.display_order,
                is_active=c.is_active,
                image_url=c.image_url,
                product_count=counts.get(c.id, (0, 0))[0],
                published_product_count=counts.get(c.id, (0, 0))[1],
                updated_at=c.updated_at,
            )
            for c in category_rows
        ]
        return Page[AdminCategoryListItem](items=items, total=total)

    async def get_category(self, category_id: uuid.UUID) -> AdminCategoryRead:
        cat = await self._require_category(category_id)
        return await self._to_read(cat)

    # ======================================================================
    # Create
    # ======================================================================

    async def create_category(
        self,
        body: AdminCategoryCreate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminCategoryRead:
        slug = await self._resolve_unique_slug(body.slug or _slugify(body.name))

        if body.parent_id is not None:
            await self._validate_parent(body.parent_id, moving_id=None)

        category = Category(
            slug=slug,
            name=body.name.strip(),
            kind=body.kind,
            description=body.description,
            image_url=body.image_url,
            image_public_id=body.image_public_id,
            parent_id=body.parent_id,
            display_order=body.display_order,
            is_active=body.is_active,
            seo_title=body.seo_title,
            seo_description=body.seo_description,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        self.session.add(category)
        try:
            await self.session.flush()
        except IntegrityError as exc:  # pragma: no cover — pre-check usually catches
            raise ConflictError(
                "Category slug collision",
                code="category_slug_conflict",
            ) from exc

        await self.audit.record(
            action=AuditAction.CREATE,
            entity_type="category",
            entity_id=category.id,
            actor_user_id=actor_user_id,
            summary=f"Created {category.kind.value} category '{category.name}'",
            metadata={
                "slug": category.slug,
                "kind": category.kind.value,
                "parent_id": str(category.parent_id) if category.parent_id else None,
            },
            request_id=request_id,
        )
        log.info(
            "admin_category_created",
            category_id=str(category.id),
            slug=category.slug,
            kind=category.kind.value,
            actor=str(actor_user_id),
        )
        return await self._to_read(category)

    # ======================================================================
    # Update
    # ======================================================================

    async def update_category(
        self,
        category_id: uuid.UUID,
        body: AdminCategoryUpdate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminCategoryRead:
        category = await self._require_category(category_id)

        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise ValidationError("Update body has no fields to apply")

        # Parent guard: pre-validate before mutation.
        if "parent_id" in changes:
            new_parent = changes["parent_id"]
            if new_parent is not None:
                await self._validate_parent(new_parent, moving_id=category.id)

        # Image swap → schedule a Cloudinary destroy on the previous asset.
        image_swap_old_public_id: str | None = None
        new_url_set = "image_url" in changes
        new_pid_set = "image_public_id" in changes
        if new_url_set or new_pid_set:
            # Validate the pair is consistent in the resulting state.
            final_url = (
                changes.get("image_url", category.image_url)
                if new_url_set
                else category.image_url
            )
            final_pid = (
                changes.get("image_public_id", category.image_public_id)
                if new_pid_set
                else category.image_public_id
            )
            if (final_url is None) != (final_pid is None):
                raise ValidationError(
                    "image_url and image_public_id must be set together "
                    "(or cleared together)",
                    code="image_pair_inconsistent",
                )
            if (
                category.image_public_id
                and category.image_public_id != changes.get("image_public_id")
            ):
                image_swap_old_public_id = category.image_public_id

        before = self._snapshot(category)

        for field, value in changes.items():
            setattr(category, field, value)
        if "name" in changes and changes["name"] is not None:
            category.name = changes["name"].strip()
        category.updated_by = actor_user_id
        await self.session.flush()

        after = self._snapshot(category)
        diff = {
            k: {"from": before[k], "to": after[k]}
            for k in changes
            if before.get(k) != after.get(k)
        }
        if diff:
            await self.audit.record(
                action=AuditAction.UPDATE,
                entity_type="category",
                entity_id=category.id,
                actor_user_id=actor_user_id,
                summary=f"Updated category '{category.name}' fields: {sorted(diff)}",
                metadata={"changed": diff},
                request_id=request_id,
            )

        # Cloudinary cleanup is BEST-EFFORT and runs AFTER the DB commit
        # boundary — failures here don't reverse a successful PATCH. A
        # future orphan-asset cleanup cron can sweep anything we miss.
        if image_swap_old_public_id is not None:
            await self._destroy_cloudinary_quiet(image_swap_old_public_id)

        return await self._to_read(category)

    # ======================================================================
    # Archive / unarchive
    # ======================================================================

    async def archive(
        self,
        category_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminCategoryRead:
        category = await self._require_category(category_id)
        if not category.is_active:
            raise ValidationError(
                "Category is already archived",
                code="status_unchanged",
            )
        category.is_active = False
        category.updated_by = actor_user_id
        await self.session.flush()
        await self._record_active_change(
            category, before=True, actor_user_id=actor_user_id, request_id=request_id,
            verb="archived",
        )
        return await self._to_read(category)

    async def unarchive(
        self,
        category_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminCategoryRead:
        category = await self._require_category(category_id)
        if category.is_active:
            raise ValidationError(
                "Category is already active",
                code="status_unchanged",
            )
        category.is_active = True
        category.updated_by = actor_user_id
        await self.session.flush()
        await self._record_active_change(
            category, before=False, actor_user_id=actor_user_id, request_id=request_id,
            verb="unarchived",
        )
        return await self._to_read(category)

    # ======================================================================
    # Reorder
    # ======================================================================

    async def reorder(
        self,
        category_id: uuid.UUID,
        body: CategoryReorderRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminCategoryRead:
        category = await self._require_category(category_id)
        if category.display_order == body.display_order:
            return await self._to_read(category)
        before = category.display_order
        category.display_order = body.display_order
        category.updated_by = actor_user_id
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="category",
            entity_id=category.id,
            actor_user_id=actor_user_id,
            summary=f"Reordered category '{category.name}' "
            f"({before} → {category.display_order})",
            metadata={"display_order": {"from": before, "to": category.display_order}},
            request_id=request_id,
        )
        return await self._to_read(category)

    # ======================================================================
    # Internal helpers
    # ======================================================================

    async def _require_category(self, category_id: uuid.UUID) -> Category:
        cat = await self.session.get(Category, category_id)
        if cat is None or cat.deleted_at is not None:
            raise NotFoundError("Category not found")
        return cat

    async def _to_read(self, category: Category) -> AdminCategoryRead:
        """Refresh + serialize.

        Server-managed timestamps (`updated_at` via `onupdate=now()`) need
        an explicit refresh after a flush, otherwise Pydantic's
        `from_attributes` materialisation triggers an implicit lazy load
        that the async session forbids (`MissingGreenlet`).
        """
        await self.session.refresh(category)
        return AdminCategoryRead.model_validate(category)

    async def _resolve_unique_slug(self, candidate: str) -> str:
        candidate = candidate.lower().strip("-")[:160]
        if not await self.categories.slug_exists(candidate):
            return candidate
        for i in range(2, 100):
            attempt = f"{candidate[:156]}-{i}"
            if not await self.categories.slug_exists(attempt):
                return attempt
        raise ConflictError(
            "Could not resolve a unique slug after 100 attempts",
            code="slug_collision_exhausted",
        )

    async def _validate_parent(
        self,
        candidate_parent_id: uuid.UUID,
        *,
        moving_id: uuid.UUID | None,
    ) -> None:
        """Reject self-parent, missing parent, descendant cycles, depth excess.

        `moving_id=None` for create (no descendants to worry about).
        """
        if moving_id is not None and candidate_parent_id == moving_id:
            raise ValidationError(
                "A category cannot be its own parent",
                code="invalid_parent_self",
            )
        parent = await self.session.get(Category, candidate_parent_id)
        if parent is None or parent.deleted_at is not None:
            raise ValidationError(
                "Parent category not found",
                code="parent_not_found",
            )

        # Cycle prevention: candidate parent must not be a descendant of
        # the node we're moving.
        if moving_id is not None:
            descendants = await self.categories.descendant_ids(moving_id)
            if candidate_parent_id in descendants:
                raise ValidationError(
                    "Cannot set a descendant as parent (would create a cycle)",
                    code="invalid_parent_cycle",
                )

        # Depth cap: the new parent's depth + 1 (this node) must fit.
        parent_depth = await self.categories.get_depth(candidate_parent_id)
        if parent_depth + 1 >= MAX_DEPTH:
            raise ValidationError(
                f"Category hierarchy capped at {MAX_DEPTH} levels",
                code="hierarchy_depth_exceeded",
                extra={"max_depth": MAX_DEPTH, "parent_depth": parent_depth},
            )

    @staticmethod
    def _snapshot(category: Category) -> dict[str, Any]:
        """Audit-friendly serialisation of mutable fields."""
        return {
            "name": category.name,
            "description": category.description,
            "image_url": category.image_url,
            "image_public_id": category.image_public_id,
            "parent_id": str(category.parent_id) if category.parent_id else None,
            "display_order": category.display_order,
            "seo_title": category.seo_title,
            "seo_description": category.seo_description,
        }

    async def _record_active_change(
        self,
        category: Category,
        *,
        before: bool,
        actor_user_id: uuid.UUID,
        request_id: str | None,
        verb: str,
    ) -> None:
        await self.audit.record(
            action=AuditAction.STATUS_CHANGED,
            entity_type="category",
            entity_id=category.id,
            actor_user_id=actor_user_id,
            summary=f"Category '{category.name}' {verb}",
            metadata={"is_active": {"from": before, "to": category.is_active}},
            request_id=request_id,
        )

    async def _destroy_cloudinary_quiet(self, public_id: str) -> None:
        """Best-effort delete; log + swallow upstream failures."""
        try:
            client = CloudinaryClient(get_settings())
            await client.destroy(public_id)
        except IntegrationError as exc:
            log.warning(
                "category_image_cleanup_failed",
                public_id=public_id,
                reason=str(exc),
            )
        except Exception:
            log.exception(
                "category_image_cleanup_unexpected",
                public_id=public_id,
            )
