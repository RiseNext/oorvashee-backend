"""Order + Payment data access."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import RazorpayPaymentStatus
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
