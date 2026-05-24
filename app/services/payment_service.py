"""PaymentService — finalises Razorpay payments.

Two entry points converge on `finalize_captured_payment`:
  1. `POST /payments/verify` — frontend calls right after Razorpay widget
     succeeds. Gives the customer instant feedback.
  2. `POST /webhooks/razorpay` — Razorpay's server-to-server retry-safe
     notification. Source of truth.

Either path:
  - Validates the HMAC signature.
  - Looks up the local `payment` row by `razorpay_order_id`.
  - Loads the matching order with FOR UPDATE on its inventory rows.
  - Consumes the reservation → real stock decrement.
  - Marks order paid + payment captured.
  - Records an audit_log entry.
  - Returns the canonical OrderRead.

Idempotency:
  - Webhook: `payments.webhook_event_id` UNIQUE — duplicate webhooks no-op.
  - Verify: middleware-level Idempotency-Key plus an already-captured check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import (
    AuthenticationError,
    IntegrationError,
    NotFoundError,
    PaymentFailedError,
)
from app.core.logging import get_logger
from app.integrations.razorpay_client import RazorpayClient
from app.models.enums import (
    AuditAction,
    PaymentStatus,
    RazorpayPaymentStatus,
)
from app.models.order import Order
from app.models.payment import Payment
from app.repositories.order_repo import OrderRepository, PaymentRepository
from app.services.audit_service import AuditService
from app.services.base import BaseService
from app.services.checkout_service import CheckoutService
from app.services.inventory_service import InventoryService, StockRequirement

log = get_logger(__name__)


class PaymentService(BaseService):
    @property
    def payments(self) -> PaymentRepository:
        return PaymentRepository(self.session)

    @property
    def orders(self) -> OrderRepository:
        return OrderRepository(self.session)

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def verify_and_capture(
        self,
        *,
        order_number: str,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
        request_id: str | None,
    ) -> Order:
        """Frontend-initiated verification + capture."""
        settings = get_settings()
        client = RazorpayClient(settings)
        if not client.verify_payment_signature(
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
        ):
            log.warning(
                "razorpay_verify_bad_signature",
                order_number=order_number,
                razorpay_order_id=razorpay_order_id,
            )
            raise AuthenticationError("Invalid Razorpay payment signature")

        payment = await self.payments.get_by_razorpay_order_id(razorpay_order_id)
        if payment is None:
            raise NotFoundError("Payment record not found for this Razorpay order")

        if payment.status == RazorpayPaymentStatus.CAPTURED:
            # Verify can be called multiple times; replay the existing state.
            log.info(
                "razorpay_verify_already_captured",
                razorpay_payment_id=razorpay_payment_id,
            )
            order = await self.orders.get_by_number(order_number)
            if order is None:
                raise NotFoundError("Order not found")
            return order

        return await self._capture(
            payment=payment,
            razorpay_payment_id=razorpay_payment_id,
            razorpay_signature=razorpay_signature,
            source="verify",
            actor_request_id=request_id,
        )

    async def handle_webhook(
        self,
        *,
        event: dict[str, Any],
        signature_ok: bool,
    ) -> dict[str, str]:
        """Razorpay webhook entry point. Always returns 200 once signature
        is verified, even for duplicate events — Razorpay retries until 2xx.
        """
        if not signature_ok:
            raise AuthenticationError("Invalid webhook signature")

        event_id = event.get("id") or event.get("event_id")
        event_type = event.get("event", "")

        if event_id and await self.payments.get_by_webhook_event_id(event_id):
            log.info("razorpay_webhook_duplicate", event_id=event_id)
            return {"status": "duplicate"}

        # Only payment.captured / payment.failed drive state today.
        if event_type == "payment.captured":
            payload = (event.get("payload") or {}).get("payment", {}).get("entity", {})
            razorpay_order_id = payload.get("order_id")
            razorpay_payment_id = payload.get("id")
            if not razorpay_order_id or not razorpay_payment_id:
                raise IntegrationError("Malformed Razorpay webhook payload")

            payment = await self.payments.get_by_razorpay_order_id(razorpay_order_id)
            if payment is None:
                log.warning(
                    "razorpay_webhook_unknown_order",
                    razorpay_order_id=razorpay_order_id,
                )
                return {"status": "unknown_order"}
            if payment.status == RazorpayPaymentStatus.CAPTURED:
                payment.webhook_event_id = event_id
                await self.session.flush()
                return {"status": "already_captured"}

            await self._capture(
                payment=payment,
                razorpay_payment_id=razorpay_payment_id,
                razorpay_signature=None,
                source="webhook",
                actor_request_id=None,
                webhook_event_id=event_id,
                raw_response=payload,
            )
            return {"status": "captured"}

        if event_type == "payment.failed":
            payload = (event.get("payload") or {}).get("payment", {}).get("entity", {})
            razorpay_order_id = payload.get("order_id")
            payment = await self.payments.get_by_razorpay_order_id(razorpay_order_id or "")
            if payment is None:
                return {"status": "unknown_order"}
            await self._mark_failed(
                payment=payment,
                event_id=event_id,
                reason=payload.get("error_description") or "Razorpay reported failure",
                raw_response=payload,
            )
            return {"status": "failed_recorded"}

        log.info("razorpay_webhook_ignored", event=event_type, event_id=event_id)
        return {"status": "ignored"}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _load_order_with_items(self, order_id) -> Order:  # type: ignore[no-untyped-def]
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found")
        return order

    async def _capture(
        self,
        *,
        payment: Payment,
        razorpay_payment_id: str,
        razorpay_signature: str | None,
        source: str,
        actor_request_id: str | None,
        webhook_event_id: str | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> Order:
        """Single capture path used by both `/verify` and the webhook."""
        order = await self._load_order_with_items(payment.order_id)

        async with self.session.begin_nested():
            requirements = [
                StockRequirement(variant_id=it.variant_id, quantity=it.quantity)
                for it in order.items
            ]
            await self.inventory.consume_reservation(
                requirements, order_id=order.id
            )

            payment.razorpay_payment_id = razorpay_payment_id
            if razorpay_signature:
                payment.razorpay_signature = razorpay_signature
            payment.status = RazorpayPaymentStatus.CAPTURED
            payment.captured_at = datetime.now(UTC)
            if webhook_event_id:
                payment.webhook_event_id = webhook_event_id
            if raw_response:
                payment.raw_response = raw_response

            order.payment_status = PaymentStatus.PAID
            if order.placed_at is None:
                order.placed_at = datetime.now(UTC)

            await self.audit.record(
                action=AuditAction.PAYMENT_CAPTURED,
                entity_type="order",
                entity_id=order.id,
                summary=f"Payment captured via {source}",
                metadata={
                    "razorpay_payment_id": razorpay_payment_id,
                    "amount": str(payment.amount),
                    "source": source,
                },
                request_id=actor_request_id,
            )

        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Duplicate webhook_event_id — race with another worker. Safe.
            log.warning(
                "razorpay_capture_integrity_race",
                event_id=webhook_event_id,
                error=str(exc),
            )
            await self.session.rollback()

        log.info(
            "payment_captured",
            order_number=order.order_number,
            razorpay_payment_id=razorpay_payment_id,
            source=source,
        )
        return order

    async def _mark_failed(
        self,
        *,
        payment: Payment,
        event_id: str | None,
        reason: str,
        raw_response: dict[str, Any] | None,
    ) -> None:
        order = await self._load_order_with_items(payment.order_id)
        async with self.session.begin_nested():
            # Release the reservation so stock isn't permanently held.
            requirements = [
                StockRequirement(variant_id=it.variant_id, quantity=it.quantity)
                for it in order.items
            ]
            from app.models.enums import StockMovementReason

            await self.inventory.release_reservation(
                requirements,
                order_id=order.id,
                reason=StockMovementReason.ORDER_CANCELLED,
            )

            payment.status = RazorpayPaymentStatus.FAILED
            payment.failure_reason = reason
            if event_id:
                payment.webhook_event_id = event_id
            if raw_response:
                payment.raw_response = raw_response
            order.payment_status = PaymentStatus.FAILED

            await self.audit.record(
                action=AuditAction.STATUS_CHANGED,
                entity_type="order",
                entity_id=order.id,
                summary="Payment failed",
                metadata={"reason": reason, "razorpay_payment_id": payment.razorpay_payment_id},
            )
        await self.session.flush()
        log.info("payment_failed", order_number=order.order_number, reason=reason)

    # ------------------------------------------------------------------
    # Convenience for routers
    # ------------------------------------------------------------------

    @staticmethod
    def order_to_read(order: Order):  # type: ignore[no-untyped-def]
        # Re-export to avoid the verify/webhook routers importing CheckoutService directly.
        return CheckoutService.serialize_order(order)


# Re-export so payment endpoints don't pull payment-related domain
# errors from elsewhere.
__all__ = ["PaymentFailedError", "PaymentService"]
