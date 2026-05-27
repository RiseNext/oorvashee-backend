"""Customer data access — admin CRM views.

Read-only. All admin customer mutations (notes, role changes via Clerk
webhook) go through the relevant service layer, not through this
repository.

Key design choices:
- The `admin_list_query` joins users to an order-aggregate subquery so
  each row already carries `orders_count`, `paid_orders_count`,
  `lifetime_value`, `last_order_at` — no N+1.
- `get_with_relationships` eager-loads profile + addresses + roles in a
  single query (selectinload). Recent orders are fetched in a separate
  small query because they need their own ordering + limit.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import (
    Integer,
    Select,
    cast,
    func,
    or_,
    select,
)
from sqlalchemy.orm import selectinload

from app.models.audit_log import AuditLog
from app.models.enums import PaymentStatus
from app.models.order import Order
from app.models.role import Role, UserRole
from app.models.user import User
from app.repositories.base import BaseRepository


class CustomerRepository(BaseRepository[User]):
    model = User

    # ======================================================================
    # Lookups
    # ======================================================================

    async def get_admin(self, user_id: uuid.UUID) -> User | None:
        """Eager-load profile + addresses + roles → roles.

        Recent orders are NOT included here — they live behind a separate
        query so the order-list pagination can run independently.
        """
        stmt = (
            select(User)
            .where(User.id == user_id)
            .options(
                selectinload(User.profile),
                selectinload(User.addresses),
                selectinload(User.roles).selectinload(UserRole.role),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ======================================================================
    # Admin list — paginated with aggregates baked in
    # ======================================================================

    def _aggregates_subquery(self) -> Select:
        """`(user_id, orders_count, paid_orders_count, lifetime_value, last_order_at)`.

        One pass over orders, grouped by user_id. Used as a LEFT JOIN
        source so users with zero orders still appear in the list.
        """
        paid_int = cast(Order.payment_status == PaymentStatus.PAID, Integer)
        return (
            select(
                Order.user_id.label("user_id"),
                func.count(Order.id).label("orders_count"),
                func.coalesce(func.sum(paid_int), 0).label("paid_orders_count"),
                func.coalesce(
                    func.sum(Order.total * paid_int), 0
                ).label("lifetime_value"),
                func.max(Order.created_at).label("last_order_at"),
            )
            .where(Order.user_id.is_not(None))
            .group_by(Order.user_id)
            .subquery("order_agg")
        )

    def admin_list_query(
        self,
        *,
        q: str | None = None,
        include_deleted: bool = False,
        has_orders: bool | None = None,
        sort: str = "newest",
    ) -> Select:
        """Build the admin customer list query.

        Returns rows of the shape:
          `(User, orders_count, paid_orders_count, lifetime_value, last_order_at)`
        — service layer maps these into `AdminCustomerListItem`.
        """
        agg = self._aggregates_subquery()
        stmt = (
            select(
                User,
                func.coalesce(agg.c.orders_count, 0).label("orders_count"),
                func.coalesce(agg.c.paid_orders_count, 0).label("paid_orders_count"),
                func.coalesce(agg.c.lifetime_value, 0).label("lifetime_value"),
                agg.c.last_order_at,
            )
            .outerjoin(agg, agg.c.user_id == User.id)
            .options(selectinload(User.profile))
        )

        if not include_deleted:
            stmt = stmt.where(User.deleted_at.is_(None))
        if q:
            from app.models.user_profile import UserProfile

            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    User.email.ilike(like),
                    User.phone.ilike(like),
                    # Search on `profile.full_name` via correlated EXISTS —
                    # avoids forcing every list row through an extra JOIN
                    # when most callers don't search by name.
                    select(UserProfile.id)
                    .where(
                        UserProfile.user_id == User.id,
                        UserProfile.full_name.ilike(like),
                    )
                    .exists(),
                )
            )

        if has_orders is True:
            stmt = stmt.where(agg.c.orders_count.is_not(None))
        elif has_orders is False:
            stmt = stmt.where(agg.c.orders_count.is_(None))

        match sort:
            case "oldest":
                stmt = stmt.order_by(User.created_at.asc(), User.id.asc())
            case "ltv_desc":
                stmt = stmt.order_by(
                    func.coalesce(agg.c.lifetime_value, 0).desc(),
                    User.id.desc(),
                )
            case "orders_desc":
                stmt = stmt.order_by(
                    func.coalesce(agg.c.orders_count, 0).desc(),
                    User.id.desc(),
                )
            case "last_order_desc":
                stmt = stmt.order_by(
                    agg.c.last_order_at.desc().nullslast(),
                    User.id.desc(),
                )
            case _:  # newest
                stmt = stmt.order_by(User.created_at.desc(), User.id.desc())

        return stmt

    async def paginate_admin_list(
        self, stmt: Select, *, limit: int, offset: int
    ) -> tuple[Sequence[tuple], int]:
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(stmt.subquery())
                )
            ).scalar_one()
        )
        rows = (await self.session.execute(stmt.limit(limit).offset(offset))).all()
        return rows, total

    # ======================================================================
    # Detail aggregates — KPI tile + recent orders
    # ======================================================================

    async def user_kpi(self, user_id: uuid.UUID) -> dict[str, int | datetime | None]:
        """Per-user KPIs in one round-trip — orders count, paid count,
        cancelled count, LTV, first/last order timestamps."""
        from app.models.enums import OrderStatus

        paid_int = cast(Order.payment_status == PaymentStatus.PAID, Integer)
        cancelled_int = cast(Order.status == OrderStatus.CANCELLED, Integer)
        stmt = (
            select(
                func.count(Order.id),
                func.coalesce(func.sum(paid_int), 0),
                func.coalesce(func.sum(cancelled_int), 0),
                func.coalesce(func.sum(Order.total * paid_int), 0),
                func.min(Order.created_at),
                func.max(Order.created_at),
            )
            .where(Order.user_id == user_id)
        )
        row = (await self.session.execute(stmt)).one()
        return {
            "orders_count": int(row[0] or 0),
            "paid_orders_count": int(row[1] or 0),
            "cancelled_orders_count": int(row[2] or 0),
            "lifetime_value": row[3] or 0,
            "first_order_at": row[4],
            "last_order_at": row[5],
        }

    async def recent_orders(
        self, user_id: uuid.UUID, *, limit: int = 5
    ) -> Sequence[Order]:
        """Most-recent N orders for the detail view's "Recent orders" widget."""
        from sqlalchemy.orm import selectinload as _selectinload

        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .options(_selectinload(Order.items))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_orders_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[Sequence[Order], int]:
        """Paginated full order history for the customer detail's
        "Order history" tab. Distinct from `recent_orders` (fixed slice)."""
        base = select(Order).where(Order.user_id == user_id)
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(base.subquery())
                )
            ).scalar_one()
        )
        rows = (
            await self.session.execute(
                base.options(selectinload(Order.items))
                .order_by(Order.created_at.desc(), Order.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return rows, total

    # ======================================================================
    # Activity timeline sources
    # ======================================================================

    async def order_events(self, user_id: uuid.UUID) -> Sequence[Order]:
        """Order rows for timeline synthesis — service maps to events."""
        stmt = (
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def audit_events_for_user(
        self, user_id: uuid.UUID
    ) -> Sequence[AuditLog]:
        """Admin notes + role changes targeting this user."""
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.entity_type == "user",
                AuditLog.entity_id == str(user_id),
            )
            .order_by(AuditLog.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def assigned_role_events(
        self, user_id: uuid.UUID
    ) -> Sequence[tuple[UserRole, Role]]:
        """`user_roles` rows + their `role` for timeline assignment events."""
        stmt = (
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id)
            .order_by(UserRole.created_at.asc())
        )
        return [(ur, r) for ur, r in (await self.session.execute(stmt)).all()]

    # ======================================================================
    # Segment summary — dashboard tile
    # ======================================================================

    async def segment_summary(
        self, *, dormant_threshold_days: int = 90
    ) -> dict[str, int]:
        """Single round-trip dashboard aggregate.

        Counts include only the non-deleted population except for the
        explicit `deleted_customers` tile.
        """
        now = datetime.now(tz=None)  # naive — comparing against naive_max in subquery

        # total_customers + new_last_30_days + deleted_customers — from `users`.
        thirty_days_ago = datetime.now(tz=None) - timedelta(days=30)
        users_stmt = select(
            func.coalesce(
                func.sum(cast(User.deleted_at.is_(None), Integer)), 0
            ),
            func.coalesce(
                func.sum(
                    cast(
                        (User.deleted_at.is_(None)) & (User.created_at >= thirty_days_ago),
                        Integer,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(cast(User.deleted_at.is_not(None), Integer)), 0
            ),
        )
        users_row = (await self.session.execute(users_stmt)).one()

        # Order-related segments — derived from a per-user aggregate.
        paid_int = cast(Order.payment_status == PaymentStatus.PAID, Integer)
        per_user = (
            select(
                Order.user_id.label("uid"),
                func.count(Order.id).label("orders"),
                func.sum(paid_int).label("paid"),
                func.max(Order.created_at).filter(
                    Order.payment_status == PaymentStatus.PAID
                ).label("last_paid_at"),
            )
            .where(Order.user_id.is_not(None))
            .group_by(Order.user_id)
            .subquery()
        )

        dormant_cutoff = datetime.now(tz=None) - timedelta(days=dormant_threshold_days)
        orders_stmt = select(
            func.count().filter(per_user.c.orders > 0),
            func.count().filter(per_user.c.paid > 0),
            func.count().filter(per_user.c.paid >= 2),
            func.count().filter(
                (per_user.c.paid > 0) & (per_user.c.last_paid_at < dormant_cutoff)
            ),
        ).select_from(per_user)
        orders_row = (await self.session.execute(orders_stmt)).one()

        del now  # `now` was for explicit-cutoff debug visibility; not used in SQL.
        return {
            "total_customers": int(users_row[0] or 0),
            "new_last_30_days": int(users_row[1] or 0),
            "deleted_customers": int(users_row[2] or 0),
            "customers_with_orders": int(orders_row[0] or 0),
            "customers_with_paid_orders": int(orders_row[1] or 0),
            "repeat_customers": int(orders_row[2] or 0),
            "dormant_customers": int(orders_row[3] or 0),
        }
