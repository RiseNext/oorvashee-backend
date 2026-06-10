"""ReservationLifecycleService — state-machine transitions for reservations.

Drives the terminal transitions of the reservation state machine and the two
expiry sweeps the cron (Phase 4) calls:

  - complete_sale(order)        RESERVED/PAYMENT_PROCESSING -> COMPLETED
                                + decrement stock_on_hand, increment sold_quantity
                                (the ONLY physical stock movement; webhook capture).
  - cancel_for_order(order)     -> CANCELLED, NO stock change (webhook failure).
  - sweep_expired_reservations()        RESERVED   + expires_at < now() -> EXPIRED
  - sweep_payment_processing_backstop() PAYMENT_PROCESSING + expires_at < now()
                                -> CANCELLED (15-min gateway-never-called-back guard)

All status flips are set-based bulk UPDATEs (no row hydration), so a single
sweep is cheap. Expiry never mutates stock — releasing a hold just means the
derived `available` rises again, automatically.
"""

from __future__ import annotations

from sqlalchemy import func, select, update

from app.core.logging import get_logger
from app.models.checkout_session import CheckoutSession
from app.models.enums import CheckoutSessionStatus, PaymentStatus, ReservationStatus
from app.models.order import Order
from app.models.reservation import Reservation
from app.services.base import BaseService
from app.services.inventory_service import InventoryService, StockRequirement

log = get_logger(__name__)


class ReservationLifecycleService(BaseService):
    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    # ------------------------------------------------------------------
    # Terminal transitions driven by the payment webhook
    # ------------------------------------------------------------------

    async def complete_sale(self, order: Order) -> None:
        """Confirmed payment for a reservation-backed order.

        Decrements physical stock + bumps sold_quantity, then flips the
        session + all its reservations to COMPLETED. `order.items` must be
        loaded. Idempotency is the caller's responsibility (PaymentService
        only calls this once per capture, guarded by payment status +
        webhook_event_id).
        """
        requirements = [
            StockRequirement(variant_id=it.variant_id, quantity=it.quantity)
            for it in order.items
        ]
        await self.inventory.commit_sale(requirements, order_id=order.id)
        if order.checkout_session_id is not None:
            await self._transition_session(
                order.checkout_session_id,
                session_status=CheckoutSessionStatus.COMPLETED,
                reservation_status=ReservationStatus.COMPLETED,
            )
        log.info("reservation_sale_completed", order_id=str(order.id))

    async def cancel_for_order(self, order: Order) -> None:
        """Payment failed/declined for a reservation-backed order.

        Flips the session + reservations to CANCELLED. NO stock change —
        stock was never decremented (decrement happens only on capture).
        """
        if order.checkout_session_id is not None:
            await self._transition_session(
                order.checkout_session_id,
                session_status=CheckoutSessionStatus.CANCELLED,
                reservation_status=ReservationStatus.CANCELLED,
            )
        log.info("reservation_session_cancelled", order_id=str(order.id))

    async def _transition_session(
        self,
        session_id,  # type: ignore[no-untyped-def]
        *,
        session_status: CheckoutSessionStatus,
        reservation_status: ReservationStatus,
    ) -> None:
        await self.session.execute(
            update(Reservation)
            .where(Reservation.checkout_session_id == session_id)
            .values(status=reservation_status, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(
            update(CheckoutSession)
            .where(CheckoutSession.id == session_id)
            .values(status=session_status, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()

    # ------------------------------------------------------------------
    # Expiry sweeps (called by the Phase 4 cron; also directly testable)
    # ------------------------------------------------------------------

    async def sweep_expired_reservations(self) -> dict[str, int]:
        """3-min rule: RESERVED rows past expires_at -> EXPIRED. Never touches
        PAYMENT_PROCESSING. No stock mutation (derived availability rises on
        its own). Sessions sharing the expiry flip ACTIVE -> EXPIRED."""
        r = await self.session.execute(
            update(Reservation)
            .where(
                Reservation.status == ReservationStatus.RESERVED,
                Reservation.expires_at < func.now(),
            )
            .values(status=ReservationStatus.EXPIRED, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        s = await self.session.execute(
            update(CheckoutSession)
            .where(
                CheckoutSession.status == CheckoutSessionStatus.ACTIVE,
                CheckoutSession.expires_at < func.now(),
            )
            .values(status=CheckoutSessionStatus.EXPIRED, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()
        counts = {"reservations": r.rowcount or 0, "sessions": s.rowcount or 0}
        if counts["reservations"] or counts["sessions"]:
            log.info("reservations_expired", **counts)
        return counts

    async def sweep_payment_processing_backstop(self) -> dict[str, int]:
        """15-min backstop: PAYMENT_PROCESSING rows past expires_at -> CANCELLED.

        Only catches gateways that never call back. NO stock change (stock is
        decremented only on capture). Any linked still-PENDING order is marked
        payment_status=FAILED. A late capture webhook can still complete the
        order afterwards (payment is truth) — commit_sale's stock guard covers
        the rare oversell race.
        """
        expired_session_ids = (
            select(CheckoutSession.id)
            .where(
                CheckoutSession.status == CheckoutSessionStatus.PAYMENT_PROCESSING,
                CheckoutSession.expires_at < func.now(),
            )
            .scalar_subquery()
        )
        await self.session.execute(
            update(Order)
            .where(
                Order.checkout_session_id.in_(expired_session_ids),
                Order.payment_status == PaymentStatus.PENDING,
            )
            .values(payment_status=PaymentStatus.FAILED, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        r = await self.session.execute(
            update(Reservation)
            .where(
                Reservation.status == ReservationStatus.PAYMENT_PROCESSING,
                Reservation.expires_at < func.now(),
            )
            .values(status=ReservationStatus.CANCELLED, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        s = await self.session.execute(
            update(CheckoutSession)
            .where(
                CheckoutSession.status == CheckoutSessionStatus.PAYMENT_PROCESSING,
                CheckoutSession.expires_at < func.now(),
            )
            .values(status=CheckoutSessionStatus.CANCELLED, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await self.session.flush()
        counts = {"reservations": r.rowcount or 0, "sessions": s.rowcount or 0}
        if counts["reservations"] or counts["sessions"]:
            log.info("payment_processing_backstopped", **counts)
        return counts
