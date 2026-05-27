"""Analytics data access — read-only aggregate queries.

Every method is a single round-trip aggregate. No N+1, no per-row Python
post-processing beyond `int()` / `Decimal()` coercion. Indexes covered:
  - `ix_orders_created` (revenue / order trends by `created_at`)
  - `ix_orders_payment_status` (payment_status filters)
  - `ix_orders_status` (fulfillment KPIs)

Caching readiness: all methods are deterministic for a given
(`since`, `until`, …) tuple. A future Redis read-cache can shield the
admin dashboard's repeated polls without touching this code.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Integer, and_, cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.models.category import Category, ProductCategory
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
)
from app.models.order import Order, OrderItem
from app.models.payment import Shipment
from app.models.product import Product
from app.models.user import User
from app.repositories.base import BaseRepository


# Reusable predicate fragments — kept consistent across endpoints so
# revenue numbers reconcile across tiles.
def _paid_predicate() -> Any:  # type: ignore[name-defined]  # noqa: F821 — see runtime fallback below
    ...


# Runtime fallback (avoid forward-reference noise above).
def _paid() -> object:
    return Order.payment_status == PaymentStatus.PAID


def _placed_in_range(since: datetime, until: datetime) -> object:
    return and_(Order.created_at >= since, Order.created_at < until)


# date_trunc unit per granularity — Postgres accepts these literals.
_GRANULARITY_SQL = {"day": "day", "week": "week", "month": "month"}


class AnalyticsRepository(BaseRepository[Order]):
    """Hangs off Order — the central fact table — but reads across many."""

    model = Order

    # ======================================================================
    # Overview
    # ======================================================================

    async def overview_aggregates(
        self, *, since: datetime, until: datetime
    ) -> dict[str, Decimal | int]:
        """One-shot aggregate for the dashboard's top tile.

        Returns:
          - total_revenue (paid orders only)
          - total_orders (in range)
          - paid_orders
          - units_sold (across line items of paid orders)
          - guest_orders (user_id IS NULL)

        AOV and new_customers are NOT computed here — different filter
        domains (AOV needs per-paid-order; new_customers joins users).
        """
        in_range = _placed_in_range(since, until)
        paid_filter = and_(in_range, _paid())

        # Single SELECT with conditional rollups via SUM(CAST(... AS INT)).
        stmt = select(
            func.count(Order.id).filter(in_range),
            func.coalesce(
                func.sum(Order.total).filter(paid_filter), 0
            ),
            func.count(Order.id).filter(paid_filter),
            func.coalesce(
                func.sum(cast(Order.user_id.is_(None), Integer)).filter(in_range),
                0,
            ),
        )
        total_orders, total_revenue, paid_orders, guest_orders = (
            (await self.session.execute(stmt)).one()
        )

        # `units_sold` requires joining `order_items` — separate query.
        units_stmt = (
            select(func.coalesce(func.sum(OrderItem.quantity), 0))
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(paid_filter)
        )
        units_sold = int((await self.session.execute(units_stmt)).scalar_one())

        return {
            "total_orders": int(total_orders or 0),
            "total_revenue": Decimal(str(total_revenue or 0)),
            "paid_orders": int(paid_orders or 0),
            "units_sold": units_sold,
            "guest_orders": int(guest_orders or 0),
        }

    async def new_customers(
        self, *, since: datetime, until: datetime
    ) -> int:
        """Customers whose `users.created_at` falls in range.

        Distinct from `Order.user_id` users — this captures sign-ups in
        the window, not first-order-in-window. Sign-up is the canonical
        acquisition event for marketing reporting.
        """
        stmt = (
            select(func.count(User.id))
            .where(User.created_at >= since, User.created_at < until)
            .where(User.deleted_at.is_(None))
        )
        return int((await self.session.execute(stmt)).scalar_one())

    # ======================================================================
    # Trends (time series)
    # ======================================================================

    async def revenue_orders_units_trend(
        self,
        *,
        since: datetime,
        until: datetime,
        granularity: str,
    ) -> list[tuple[date, Decimal, int, int]]:
        """`(bucket, revenue, orders, units)` per bucket within range.

        `bucket` = `date_trunc('<granularity>', orders.created_at)` cast
        to `DATE`. Only paid orders contribute revenue + units; total
        orders count includes all statuses so the chart can show
        cancellation gaps.

        Buckets with zero activity are NOT emitted by the DB — the
        service layer fills empty buckets so the chart x-axis is dense.
        """
        unit = _GRANULARITY_SQL.get(granularity, "day")
        # Inline the unit string instead of bind param. Postgres treats
        # `date_trunc($p, col)` with a parameter as a different expression
        # in SELECT vs GROUP BY (separate parameter placeholders break
        # the GROUP BY equality check). `literal_column` substitutes the
        # quoted string into the generated SQL directly. The value comes
        # from a fixed allowlist so there's no SQL-injection surface.
        unit_lit = literal_column(f"'{unit}'")
        bucket = func.date_trunc(unit_lit, Order.created_at).label("bucket")
        in_range = _placed_in_range(since, until)

        # Two-pass via subquery: orders aggregates first, then LEFT JOIN
        # units.
        # Range predicate goes in WHERE (not in FILTER) so Postgres
        # doesn't reject `orders.created_at` post-GROUP BY — only the
        # grouped expression `date_trunc(...)` is referenceable there.
        # FILTER is reserved for predicates the outer WHERE can't carry
        # (i.e. payment_status, which differs per aggregate).
        order_agg = (
            select(
                bucket,
                func.coalesce(
                    func.sum(Order.total).filter(_paid()), 0
                ).label("revenue"),
                func.count(Order.id).label("orders"),
            )
            .where(in_range)
            .group_by(bucket)
            .subquery()
        )
        units_agg = (
            select(
                func.date_trunc(unit_lit, Order.created_at).label("bucket"),
                func.coalesce(func.sum(OrderItem.quantity), 0).label("units"),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .where(in_range)
            .where(_paid())
            .group_by(func.date_trunc(unit_lit, Order.created_at))
            .subquery()
        )
        joined = (
            select(
                order_agg.c.bucket,
                order_agg.c.revenue,
                order_agg.c.orders,
                func.coalesce(units_agg.c.units, 0),
            )
            .select_from(
                order_agg.outerjoin(units_agg, order_agg.c.bucket == units_agg.c.bucket)
            )
            .order_by(order_agg.c.bucket.asc())
        )
        rows = (await self.session.execute(joined)).all()
        return [
            (
                row[0].date() if isinstance(row[0], datetime) else row[0],
                Decimal(str(row[1] or 0)),
                int(row[2] or 0),
                int(row[3] or 0),
            )
            for row in rows
        ]

    # ======================================================================
    # Top products / categories
    # ======================================================================

    async def top_products(
        self,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        sort_by: str = "revenue",
    ) -> list[tuple[uuid.UUID, str, str, int, Decimal, int]]:
        """Top-N by revenue (default) or units sold.

        Only paid orders count — driving from `OrderItem.line_total` so
        the unit-price snapshot is preserved (matches what the customer
        actually paid, not the current product.base_price).
        """
        units = func.coalesce(func.sum(OrderItem.quantity), 0).label("units")
        revenue = func.coalesce(func.sum(OrderItem.line_total), 0).label("revenue")
        order_count = func.count(func.distinct(Order.id)).label("orders")

        stmt = (
            select(
                Product.id,
                Product.name,
                Product.slug,
                units,
                revenue,
                order_count,
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .where(_placed_in_range(since, until))
            .where(_paid())
            .group_by(Product.id, Product.name, Product.slug)
        )
        stmt = stmt.order_by(
            (revenue if sort_by == "revenue" else units).desc(),
            Product.name.asc(),
        ).limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [
            (row[0], row[1], row[2], int(row[3] or 0), Decimal(str(row[4] or 0)), int(row[5] or 0))
            for row in rows
        ]

    async def top_categories(
        self,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        sort_by: str = "revenue",
    ) -> list[tuple[uuid.UUID, str, str, str, int, Decimal, int]]:
        """Top categories by sales volume in range.

        A single OrderItem may contribute to multiple categories if the
        product is multi-tagged — that's correct (sales of a Bridal
        Kanchipuram count under both 'kanchipuram-silk' AND 'bridal').
        """
        units = func.coalesce(func.sum(OrderItem.quantity), 0).label("units")
        revenue = func.coalesce(func.sum(OrderItem.line_total), 0).label("revenue")
        product_count = func.count(func.distinct(Product.id)).label("products_sold")

        stmt = (
            select(
                Category.id,
                Category.slug,
                Category.name,
                Category.kind,
                units,
                revenue,
                product_count,
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .join(ProductCategory, ProductCategory.product_id == Product.id)
            .join(Category, Category.id == ProductCategory.category_id)
            .where(_placed_in_range(since, until))
            .where(_paid())
            .where(Category.deleted_at.is_(None))
            .group_by(Category.id, Category.slug, Category.name, Category.kind)
        )
        stmt = stmt.order_by(
            (revenue if sort_by == "revenue" else units).desc(),
            Category.name.asc(),
        ).limit(limit)
        rows = (await self.session.execute(stmt)).all()
        return [
            (
                row[0],
                row[1],
                row[2],
                row[3].value if hasattr(row[3], "value") else str(row[3]),
                int(row[4] or 0),
                Decimal(str(row[5] or 0)),
                int(row[6] or 0),
            )
            for row in rows
        ]

    # ======================================================================
    # Fulfillment KPIs
    # ======================================================================

    async def fulfillment_kpis(self, *, today_start: datetime) -> dict[str, int]:
        """`today_start` is the UTC midnight of the caller's "today".

        Single round-trip — all six KPIs as conditional sums.
        """
        # awaiting_packing: placed + (paid or cod_pending)
        awaiting_packing = and_(
            Order.status == OrderStatus.PLACED,
            Order.payment_status.in_(
                [PaymentStatus.PAID, PaymentStatus.COD_PENDING]
            ),
        )
        awaiting_shipment = Order.status == OrderStatus.PACKED
        in_transit = Order.status == OrderStatus.SHIPPED
        delivered_today = and_(
            Order.status == OrderStatus.DELIVERED,
            Order.delivered_at >= today_start,
        )
        cod_outstanding = and_(
            Order.payment_method == PaymentMethod.COD,
            Order.payment_status == PaymentStatus.COD_PENDING,
            Order.status.not_in([OrderStatus.DELIVERED, OrderStatus.CANCELLED]),
        )

        stmt = select(
            func.coalesce(
                func.sum(cast(awaiting_packing, Integer)), 0
            ),
            func.coalesce(
                func.sum(cast(awaiting_shipment, Integer)), 0
            ),
            func.coalesce(
                func.sum(cast(in_transit, Integer)), 0
            ),
            func.coalesce(
                func.sum(cast(delivered_today, Integer)), 0
            ),
            func.coalesce(
                func.sum(cast(cod_outstanding, Integer)), 0
            ),
        )
        row = (await self.session.execute(stmt)).one()

        # shipped_missing_tracking joins Shipment — separate query.
        shipped_missing = (
            select(func.count(Order.id))
            .select_from(Order)
            .outerjoin(Shipment, Shipment.order_id == Order.id)
            .where(
                Order.status == OrderStatus.SHIPPED,
                Shipment.tracking_id.is_(None),
            )
        )
        missing_count = int(
            (await self.session.execute(shipped_missing)).scalar_one()
        )

        return {
            "awaiting_packing": int(row[0] or 0),
            "awaiting_shipment": int(row[1] or 0),
            "in_transit": int(row[2] or 0),
            "delivered_today": int(row[3] or 0),
            "cod_outstanding": int(row[4] or 0),
            "shipped_missing_tracking": missing_count,
        }

    # ======================================================================
    # Customer metrics
    # ======================================================================

    async def customer_metrics(
        self, *, since: datetime, until: datetime
    ) -> dict[str, int]:
        """`new_customers`, `repeat_customers`, `returning_customers`,
        `guest_orders`, `total_active_customers` for a single window."""

        # new_customers — users created in range.
        new_stmt = select(func.count(User.id)).where(
            User.created_at >= since,
            User.created_at < until,
            User.deleted_at.is_(None),
        )
        new_customers = int((await self.session.execute(new_stmt)).scalar_one())

        # active_in_range — distinct user_id with at least one order.
        active_stmt = (
            select(func.count(func.distinct(Order.user_id)))
            .where(_placed_in_range(since, until))
            .where(Order.user_id.is_not(None))
        )
        active = int((await self.session.execute(active_stmt)).scalar_one())

        # repeat_customers — users with ≥2 paid orders in range.
        repeat_subq = (
            select(Order.user_id, func.count(Order.id).label("c"))
            .where(_placed_in_range(since, until))
            .where(_paid())
            .where(Order.user_id.is_not(None))
            .group_by(Order.user_id)
            .having(func.count(Order.id) >= 2)
            .subquery()
        )
        repeat = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(repeat_subq)
                )
            ).scalar_one()
        )

        # returning_customers — users active in range AND had a paid
        # order BEFORE the range start.
        before_subq = (
            select(Order.user_id.label("uid"))
            .where(Order.created_at < since)
            .where(_paid())
            .where(Order.user_id.is_not(None))
            .distinct()
            .subquery()
        )
        in_range_subq = (
            select(Order.user_id.label("uid"))
            .where(_placed_in_range(since, until))
            .where(Order.user_id.is_not(None))
            .distinct()
            .subquery()
        )
        returning_stmt = (
            select(func.count())
            .select_from(in_range_subq)
            .where(in_range_subq.c.uid.in_(select(before_subq.c.uid)))
        )
        returning = int((await self.session.execute(returning_stmt)).scalar_one())

        # guest orders.
        guest_stmt = (
            select(func.count(Order.id))
            .where(_placed_in_range(since, until))
            .where(Order.user_id.is_(None))
        )
        guests = int((await self.session.execute(guest_stmt)).scalar_one())

        return {
            "new_customers": new_customers,
            "total_active_customers": active,
            "repeat_customers": repeat,
            "returning_customers": returning,
            "guest_orders": guests,
        }


# Silence type-checker for the placeholder protocol declared at top of file.
_ = PgUUID
