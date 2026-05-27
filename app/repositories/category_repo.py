"""Category data access — public catalog reads + admin queries."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Integer, Select, cast, func, select

from app.models.category import Category, ProductCategory
from app.models.enums import CategoryKind, ProductStatus
from app.models.product import Product
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository[Category]):
    model = Category

    # ---------- Public catalog ----------------------------------------------

    async def list_active(self) -> Sequence[Category]:
        stmt = (
            select(Category)
            .where(
                Category.deleted_at.is_(None),
                Category.is_active.is_(True),
            )
            .order_by(Category.kind, Category.display_order, Category.name)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_slug(self, slug: str) -> Category | None:
        stmt = select(Category).where(
            Category.slug == slug,
            Category.deleted_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- Admin reads -------------------------------------------------

    async def slug_exists(self, slug: str) -> bool:
        """UNIQUE check before INSERT for a clean 409.

        Soft-deleted rows still count — the UNIQUE constraint applies
        regardless of `deleted_at`, so a colliding slug means we can't
        reuse it without first restoring the original row.
        """
        stmt = select(func.count()).select_from(Category).where(Category.slug == slug)
        return int((await self.session.execute(stmt)).scalar_one()) > 0

    def admin_list_query(
        self,
        *,
        q: str | None = None,
        kinds: list[CategoryKind] | None = None,
        is_active: bool | None = None,
        parent_id: uuid.UUID | None = None,
        include_deleted: bool = False,
    ) -> Select:
        """Build the admin list base query.

        Admin can see archived (`is_active=false`) rows by default. Only
        explicit `deleted_at IS NOT NULL` rows are filtered out unless
        `include_deleted=True`.
        """
        stmt = select(Category)
        if not include_deleted:
            stmt = stmt.where(Category.deleted_at.is_(None))
        if kinds:
            stmt = stmt.where(Category.kind.in_(kinds))
        if is_active is not None:
            stmt = stmt.where(Category.is_active.is_(is_active))
        if parent_id is not None:
            stmt = stmt.where(Category.parent_id == parent_id)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                Category.name.ilike(like) | Category.slug.ilike(like)
            )
        return stmt.order_by(
            Category.kind,
            Category.display_order,
            Category.name,
        )

    async def admin_product_counts(
        self, category_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """Per-category `(total_count, published_count)` in one round-trip.

        - Total: every linked product, regardless of status.
        - Published: subset whose `status='published'` — i.e. catalog-live.
          Used by the admin list to show "X products (Y live)".
        """
        if not category_ids:
            return {}
        published_int = cast(Product.status == ProductStatus.PUBLISHED, Integer)
        stmt = (
            select(
                ProductCategory.category_id,
                func.count(Product.id),
                func.sum(published_int),
            )
            .select_from(ProductCategory)
            .join(Product, Product.id == ProductCategory.product_id)
            .where(ProductCategory.category_id.in_(category_ids))
            .group_by(ProductCategory.category_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: (int(row[1]), int(row[2] or 0)) for row in rows}

    async def descendant_ids(self, root_id: uuid.UUID) -> set[uuid.UUID]:
        """All ids in the subtree rooted at `root_id` (exclusive of the root).

        Used by the parent-cycle guard: setting a new parent for a node
        must reject any candidate parent that is itself a descendant of
        the moving node. Iterative walk in Python — fine at the small
        fan-out we expect for category hierarchies (tens, not thousands).
        Postgres `WITH RECURSIVE` would be a future optimisation if depth
        explodes.
        """
        result: set[uuid.UUID] = set()
        frontier: set[uuid.UUID] = {root_id}
        while frontier:
            stmt = select(Category.id).where(Category.parent_id.in_(frontier))
            children = {
                cid for (cid,) in (await self.session.execute(stmt)).all()
            }
            # Cull anything we've already visited (defensive against any
            # pre-existing cycle in data).
            children -= result
            children.discard(root_id)
            result |= children
            frontier = children
        return result

    async def get_depth(self, category_id: uuid.UUID) -> int:
        """Distance from this category to its root ancestor.

        Iterative walk; bounded against `MAX_DEPTH` in the service.
        """
        depth = 0
        current: uuid.UUID | None = category_id
        seen: set[uuid.UUID] = set()
        while current is not None:
            if current in seen:
                # Pre-existing cycle in data — bail rather than loop forever.
                return depth
            seen.add(current)
            stmt = select(Category.parent_id).where(Category.id == current)
            current = (await self.session.execute(stmt)).scalar_one_or_none()
            if current is not None:
                depth += 1
        return depth
