"""Order + Payment data access."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Integer, Select, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    RazorpayPaymentStatus,
)
from app.models.order import Order, OrderItem
from app.models.payment import Payment, Shipment
from app.repositories.base import BaseRepository


class OrderRepository(BaseRepository[Order]):
    model = Order

    # ---------- Reads ----------

    async def get_by_id_full(self, order_id: uuid.UUID) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.items),
                selectinload(Order.payments),
                selectinload(Order.shipment),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_number(self, order_number: str) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.order_number == order_number)
            .options(
                selectinload(Order.items),
                selectinload(Order.payments),
                selectinload(Order.shipment),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[Sequence[Order], int]:
        from sqlalchemy import func

        base = select(Order).where(Order.user_id == user_id)
        total = int(
            (await self.session.execute(
                select(func.count()).select_from(base.subquery())
            )).scalar_one()
        )
        rows = (
            await self.session.execute(
                base.options(
                    selectinload(Order.items),
                    selectinload(Order.shipment),
                )
                .order_by(Order.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return rows, total

    # ---------- Admin reads --------------------------------------------

    async def get_admin(self, order_id: uuid.UUID) -> Order | None:
        """Full eager-loaded fetch for admin detail views."""
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.items),
                selectinload(Order.payments),
                selectinload(Order.shipment),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def admin_list_query(
        self,
        *,
        q: str | None = None,
        statuses: list[OrderStatus] | None = None,
        payment_statuses: list[PaymentStatus] | None = None,
        payment_method: PaymentMethod | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        has_shipment: bool | None = None,
        sort: str = "newest",
    ) -> Select:
        """Build the admin orders list query.

        - `q` matches order_number, email, or customer_name (case-insensitive).
        - `since`/`until` filter on `created_at` (server's record of receipt;
          `placed_at` is null until payment captures for Razorpay orders).
        - `has_shipment` uses a correlated EXISTS so unfulfilled rows can
          be surfaced quickly for the "to ship today" tile.
        """
        stmt = select(Order)
        if statuses:
            stmt = stmt.where(Order.status.in_(statuses))
        if payment_statuses:
            stmt = stmt.where(Order.payment_status.in_(payment_statuses))
        if payment_method is not None:
            stmt = stmt.where(Order.payment_method == payment_method)
        if since is not None:
            stmt = stmt.where(Order.created_at >= since)
        if until is not None:
            stmt = stmt.where(Order.created_at <= until)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    Order.order_number.ilike(like),
                    Order.email.ilike(like),
                    Order.customer_name.ilike(like),
                )
            )
        if has_shipment is True:
            stmt = stmt.where(
                select(Shipment.id)
                .where(Shipment.order_id == Order.id)
                .exists()
            )
        elif has_shipment is False:
            stmt = stmt.where(
                ~select(Shipment.id)
                .where(Shipment.order_id == Order.id)
                .exists()
            )

        match sort:
            case "oldest":
                stmt = stmt.order_by(Order.created_at.asc(), Order.id.asc())
            case "total_desc":
                stmt = stmt.order_by(Order.total.desc(), Order.id.desc())
            case "total_asc":
                stmt = stmt.order_by(Order.total.asc(), Order.id.asc())
            case _:  # newest (default)
                stmt = stmt.order_by(Order.created_at.desc(), Order.id.desc())
        return stmt

    async def admin_list_with_aggregates(
        self, stmt: Select, *, limit: int, offset: int
    ) -> tuple[Sequence[Order], dict[uuid.UUID, tuple[int, bool]], int]:
        """Return rows + `{order_id: (item_count, has_shipment)}` + total.

        Two extra round-trips beyond the page fetch: one COUNT for total,
        one GROUP BY for the item_count / has_shipment aggregates. No N+1.
        """
        total = int(
            (
                await self.session.execute(
                    select(func.count()).select_from(stmt.subquery())
                )
            ).scalar_one()
        )
        rows = (
            await self.session.execute(stmt.limit(limit).offset(offset))
        ).scalars().all()

        order_ids = [o.id for o in rows]
        if not order_ids:
            return rows, {}, total

        # Single aggregate query: item_count per order + a 0/1 shipment flag.
        item_count_stmt = (
            select(OrderItem.order_id, func.count(OrderItem.id))
            .where(OrderItem.order_id.in_(order_ids))
            .group_by(OrderItem.order_id)
        )
        item_count_map = {
            row[0]: int(row[1])
            for row in (await self.session.execute(item_count_stmt)).all()
        }

        ship_stmt = select(Shipment.order_id).where(
            Shipment.order_id.in_(order_ids)
        )
        shipped_set = {
            row[0] for row in (await self.session.execute(ship_stmt)).all()
        }

        agg = {
            oid: (item_count_map.get(oid, 0), oid in shipped_set)
            for oid in order_ids
        }
        return rows, agg, total

    async def status_summary(self) -> dict[str, int | float]:
        """Single round-trip dashboard aggregate.

        SUM(CAST(condition AS INTEGER)) gives branchless conditional counts;
        cheaper than separate filtered queries. SUM(total) is partial — only
        rows whose payment_status=PAID contribute to revenue.
        """
        stmt = select(
            func.count(Order.id),
            func.sum(cast(Order.status == OrderStatus.PLACED, Integer)),
            func.sum(cast(Order.status == OrderStatus.PACKED, Integer)),
            func.sum(cast(Order.status == OrderStatus.SHIPPED, Integer)),
            func.sum(cast(Order.status == OrderStatus.DELIVERED, Integer)),
            func.sum(cast(Order.status == OrderStatus.CANCELLED, Integer)),
            func.sum(cast(Order.payment_status == PaymentStatus.PENDING, Integer)),
            func.sum(cast(Order.payment_status == PaymentStatus.COD_PENDING, Integer)),
            # awaiting_fulfillment: status=placed AND payment_status in (paid, cod_pending)
            func.sum(
                cast(
                    (Order.status == OrderStatus.PLACED)
                    & (
                        Order.payment_status.in_(
                            [PaymentStatus.PAID, PaymentStatus.COD_PENDING]
                        )
                    ),
                    Integer,
                )
            ),
            func.sum(
                cast(Order.status == OrderStatus.PACKED, Integer)
            ),
            func.coalesce(
                func.sum(
                    cast(
                        Order.payment_status == PaymentStatus.PAID,
                        Integer,
                    )
                    * Order.total
                ),
                0,
            ),
        )
        row = (await self.session.execute(stmt)).one()
        return {
            "total_orders": int(row[0]),
            "placed": int(row[1] or 0),
            "packed": int(row[2] or 0),
            "shipped": int(row[3] or 0),
            "delivered": int(row[4] or 0),
            "cancelled": int(row[5] or 0),
            "pending_payment": int(row[6] or 0),
            "cod_pending": int(row[7] or 0),
            "awaiting_fulfillment": int(row[8] or 0),
            "pending_shipment": int(row[9] or 0),
            "total_revenue_paid": float(row[10] or 0),
        }

    # ---------- Internals used by checkout ----------

    async def get_lock_friendly(self, order_id: uuid.UUID) -> Order | None:
        """Lightweight fetch — just items, no payment/shipment side joins.

        Used inside the locking critical section so we don't ping extra
        tables while holding inventory row locks.
        """
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class PaymentRepository(BaseRepository[Payment]):
    model = Payment

    async def get_by_razorpay_order_id(
        self, razorpay_order_id: str
    ) -> Payment | None:
        stmt = select(Payment).where(Payment.razorpay_order_id == razorpay_order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_webhook_event_id(
        self, webhook_event_id: str
    ) -> Payment | None:
        stmt = select(Payment).where(Payment.webhook_event_id == webhook_event_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_order(self, order_id: uuid.UUID) -> Sequence[Payment]:
        stmt = (
            select(Payment)
            .where(Payment.order_id == order_id)
            .order_by(Payment.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def latest_captured_for_order(self, order_id: uuid.UUID) -> Payment | None:
        stmt = (
            select(Payment)
            .where(
                Payment.order_id == order_id,
                Payment.status == RazorpayPaymentStatus.CAPTURED,
            )
            .order_by(Payment.captured_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class ShipmentRepository(BaseRepository[Shipment]):
    model = Shipment

    async def get_for_order(self, order_id: uuid.UUID) -> Shipment | None:
        stmt = select(Shipment).where(Shipment.order_id == order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "OrderItem",
    "OrderRepository",
    "PaymentRepository",
    "ShipmentRepository",
]
