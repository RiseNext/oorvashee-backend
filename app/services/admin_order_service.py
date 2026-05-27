"""AdminOrderService — admin-facing order lifecycle + timeline + queries.

This service is the SINGLE place where admin order mutations happen.
Customer-side order creation and payment finalisation remain owned by
`CheckoutService` and `PaymentService` — admin endpoints never write to
the `payments` table.

State machine (allowed admin moves):
    placed ──pack──> packed ──ship──> shipped ──deliver──> delivered
       │              │                  │
       └──cancel──────┼──────────────────┘
                      ▼
                  cancelled  (terminal)

Transitions enforce gating predicates:
- `mark_packed` requires payment_status ∈ {paid, cod_pending}
- `mark_shipped` requires a non-empty courier_name + tracking_id
- `mark_delivered` for COD orders auto-flips payment_status → paid
- `cancel_order` restocks all line items via InventoryService and flags
  the audit row with `requires_refund=true` when payment_status was PAID
  (Phase 4 picks up the flag for the actual Razorpay refund call)
- Already-at-target transitions are idempotent no-ops (return existing
  order), not errors — admin can safely re-click a button.

Timeline: synthesised from three sources (order timestamp columns +
payments rows + audit_logs for entity_type=order), sorted DESC by `at`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.pagination import OffsetParams, Page
from app.models.audit_log import AuditLog
from app.models.enums import (
    AuditAction,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    RazorpayPaymentStatus,
    StockMovementReason,
)
from app.models.order import Order
from app.models.payment import Payment, Shipment
from app.repositories.order_repo import OrderRepository
from app.schemas.admin_order import (
    AddOrderNoteRequest,
    AdminOrderDetail,
    AdminOrderItemRead,
    AdminOrderListItem,
    AdminPaymentRead,
    AdminShipmentRead,
    CancelOrderRequest,
    OrderStatusSummary,
    OrderTimelineEvent,
    OrderTimelineEventType,
    ShipmentUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService
from app.services.inventory_service import (
    InventoryService,
    StockRequirement,
)

log = get_logger(__name__)


# Statuses an admin may TARGET. The state-machine fans out from each
# current state; this constant exists so a future feature flag can
# selectively un-block specific moves without touching the matrix.
_TERMINAL_STATES = frozenset({OrderStatus.DELIVERED, OrderStatus.CANCELLED})


class AdminOrderService(BaseService):
    @property
    def orders(self) -> OrderRepository:
        return OrderRepository(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    # ======================================================================
    # Reads
    # ======================================================================

    async def list_orders(
        self,
        *,
        params: OffsetParams,
        q: str | None,
        statuses: list[OrderStatus] | None,
        payment_statuses: list[PaymentStatus] | None,
        payment_method: PaymentMethod | None,
        since: datetime | None,
        until: datetime | None,
        has_shipment: bool | None,
        sort: str,
    ) -> Page[AdminOrderListItem]:
        stmt = self.orders.admin_list_query(
            q=q,
            statuses=statuses,
            payment_statuses=payment_statuses,
            payment_method=payment_method,
            since=since,
            until=until,
            has_shipment=has_shipment,
            sort=sort,
        )
        rows, agg, total = await self.orders.admin_list_with_aggregates(
            stmt, limit=params.page_size, offset=params.offset
        )
        items = [
            AdminOrderListItem(
                id=o.id,
                order_number=o.order_number,
                user_id=o.user_id,
                customer_name=o.customer_name,
                email=o.email,
                phone=o.phone,
                status=o.status,
                payment_status=o.payment_status,
                payment_method=o.payment_method,
                total=o.total,
                currency=o.currency,
                item_count=agg.get(o.id, (0, False))[0],
                has_shipment=agg.get(o.id, (0, False))[1],
                placed_at=o.placed_at,
                created_at=o.created_at,
            )
            for o in rows
        ]
        return Page[AdminOrderListItem](items=items, total=total)

    async def get_order_detail(
        self, order_id: uuid.UUID, *, include_timeline: bool = True
    ) -> AdminOrderDetail:
        order = await self.orders.get_admin(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        timeline = (
            await self.build_timeline(order) if include_timeline else []
        )
        return self._to_detail(order, timeline)

    async def status_summary(self) -> OrderStatusSummary:
        agg = await self.orders.status_summary()
        return OrderStatusSummary(
            placed=agg["placed"],
            packed=agg["packed"],
            shipped=agg["shipped"],
            delivered=agg["delivered"],
            cancelled=agg["cancelled"],
            pending_payment=agg["pending_payment"],
            cod_pending=agg["cod_pending"],
            awaiting_fulfillment=agg["awaiting_fulfillment"],
            pending_shipment=agg["pending_shipment"],
            total_orders=agg["total_orders"],
            total_revenue_paid=Decimal(str(agg["total_revenue_paid"])),
        )

    # ======================================================================
    # Lifecycle transitions
    # ======================================================================

    async def mark_packed(
        self,
        order_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminOrderDetail:
        order = await self._require_order(order_id)
        if order.status is OrderStatus.PACKED:
            return await self._to_detail_loaded(order)  # idempotent
        self._assert_transition(order.status, OrderStatus.PACKED)

        # Payment gate — won't pack an unpaid Razorpay order.
        if order.payment_method is PaymentMethod.RAZORPAY:
            if order.payment_status is not PaymentStatus.PAID:
                raise ConflictError(
                    "Cannot pack a Razorpay order before payment is captured",
                    code="payment_not_captured",
                    extra={"payment_status": order.payment_status.value},
                )
        elif (
            order.payment_method is PaymentMethod.COD
            and order.payment_status
            not in (PaymentStatus.COD_PENDING, PaymentStatus.PAID)
        ):
            # COD: PLACED orders carry `cod_pending`. Must not be FAILED.
            raise ConflictError(
                "Cannot pack a COD order with non-pending payment state",
                code="invalid_payment_state",
                extra={"payment_status": order.payment_status.value},
            )

        before = order.status
        order.status = OrderStatus.PACKED
        order.packed_at = datetime.now(UTC)
        await self.session.flush()
        await self._audit_transition(
            order, before, actor_user_id, request_id, verb="packed"
        )
        return await self._to_detail_loaded(order)

    async def mark_shipped(
        self,
        order_id: uuid.UUID,
        body: ShipmentUpdateRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminOrderDetail:
        order = await self._require_order(order_id)
        if order.status is OrderStatus.SHIPPED:
            return await self._to_detail_loaded(order)
        self._assert_transition(order.status, OrderStatus.SHIPPED)

        # Shipment metadata gate — can't ship without courier + tracking.
        if not body.courier_name or not body.tracking_id:
            raise ValidationError(
                "courier_name and tracking_id are required to mark an order shipped",
                code="shipment_metadata_required",
            )

        now = datetime.now(UTC)
        shipment = await self._get_or_create_shipment(order)
        shipment.courier_name = body.courier_name
        shipment.tracking_id = body.tracking_id
        shipment.tracking_url = body.tracking_url
        shipment.shipped_at = now

        before = order.status
        order.status = OrderStatus.SHIPPED
        order.shipped_at = now
        await self.session.flush()

        await self._audit_transition(
            order,
            before,
            actor_user_id,
            request_id,
            verb="shipped",
            extra_meta={
                "courier_name": shipment.courier_name,
                "tracking_id": shipment.tracking_id,
            },
        )
        return await self._to_detail_loaded(order)

    async def mark_delivered(
        self,
        order_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminOrderDetail:
        order = await self._require_order(order_id)
        if order.status is OrderStatus.DELIVERED:
            return await self._to_detail_loaded(order)
        self._assert_transition(order.status, OrderStatus.DELIVERED)

        now = datetime.now(UTC)
        before = order.status
        before_payment = order.payment_status
        order.status = OrderStatus.DELIVERED
        order.delivered_at = now

        # Shipment row should already exist (mark_shipped created it),
        # but defensively handle a hand-skip scenario.
        shipment = await self._get_or_create_shipment(order)
        shipment.delivered_at = now

        # COD auto-paid on delivery: by definition the cash was collected
        # when the courier handed the parcel over.
        cod_auto_paid = False
        if (
            order.payment_method is PaymentMethod.COD
            and order.payment_status is PaymentStatus.COD_PENDING
        ):
            order.payment_status = PaymentStatus.PAID
            cod_auto_paid = True

        await self.session.flush()
        await self._audit_transition(
            order,
            before,
            actor_user_id,
            request_id,
            verb="delivered",
            extra_meta={
                "cod_auto_paid": cod_auto_paid,
                "payment_status_after": order.payment_status.value,
                "payment_status_before": before_payment.value,
            },
        )
        return await self._to_detail_loaded(order)

    async def cancel_order(
        self,
        order_id: uuid.UUID,
        body: CancelOrderRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminOrderDetail:
        order = await self._require_order(order_id)
        if order.status is OrderStatus.CANCELLED:
            return await self._to_detail_loaded(order)  # idempotent

        # DELIVERED cancellation = a refund/return flow, which is Phase 4.
        if order.status is OrderStatus.DELIVERED:
            raise ConflictError(
                "Cannot cancel a delivered order via this endpoint — "
                "returns/refunds flow lands in Phase 4",
                code="cannot_cancel_delivered",
            )

        # Restock — drop the inventory back. Uses the same locking
        # primitive checkout uses, so a racing reservation can't interleave.
        restock_requirements = [
            StockRequirement(variant_id=item.variant_id, quantity=item.quantity)
            for item in order.items
        ]
        if restock_requirements:
            await self.inventory.restock(
                restock_requirements,
                order_id=order.id,
                actor_user_id=actor_user_id,
                reason=StockMovementReason.ORDER_CANCELLED,
            )

        before = order.status
        before_payment = order.payment_status
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(UTC)

        # Refund flag: only meaningful when money was actually captured.
        # COD-pending cancellations have nothing to refund.
        requires_refund = order.payment_status is PaymentStatus.PAID

        await self.session.flush()
        await self.audit.record(
            action=AuditAction.STATUS_CHANGED,
            entity_type="order",
            entity_id=order.id,
            actor_user_id=actor_user_id,
            summary=f"Order {order.order_number} cancelled — {body.reason[:80]}",
            metadata={
                "from": before.value,
                "to": OrderStatus.CANCELLED.value,
                "reason": body.reason,
                "notify_customer": body.notify_customer,
                "payment_status_before": before_payment.value,
                "requires_refund": requires_refund,
                "restocked_items": len(restock_requirements),
            },
            request_id=request_id,
        )
        log.info(
            "admin_order_cancelled",
            order_number=order.order_number,
            actor=str(actor_user_id),
            requires_refund=requires_refund,
            restocked_items=len(restock_requirements),
        )

        # Recompute parent products' availability — restocking may flip
        # UNAVAILABLE products back to PUBLISHED.
        product_ids = {item.product_id for item in order.items}
        for pid in product_ids:
            await self.inventory.recompute_product_availability(
                pid, actor_user_id=actor_user_id, request_id=request_id
            )

        return await self._to_detail_loaded(order)

    # ======================================================================
    # COD payment + shipment metadata + notes
    # ======================================================================

    async def mark_cod_paid(
        self,
        order_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminOrderDetail:
        """Flip COD orders' payment_status COD_PENDING → PAID.

        Useful when the admin records cash receipt without yet marking the
        order delivered (rare, but supported). For the common case, mark
        delivered does this automatically.
        """
        order = await self._require_order(order_id)
        if order.payment_method is not PaymentMethod.COD:
            raise ConflictError(
                "Only COD orders can be marked paid manually",
                code="not_a_cod_order",
            )
        if order.payment_status is PaymentStatus.PAID:
            return await self._to_detail_loaded(order)
        if order.payment_status is not PaymentStatus.COD_PENDING:
            raise ConflictError(
                "Only COD orders in cod_pending state can be marked paid",
                code="invalid_payment_state",
                extra={"payment_status": order.payment_status.value},
            )

        before = order.payment_status
        order.payment_status = PaymentStatus.PAID
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.PAYMENT_CAPTURED,
            entity_type="order",
            entity_id=order.id,
            actor_user_id=actor_user_id,
            summary=f"Order {order.order_number} COD payment recorded",
            metadata={
                "payment_method": "cod",
                "payment_status_before": before.value,
                "payment_status_after": order.payment_status.value,
            },
            request_id=request_id,
        )
        return await self._to_detail_loaded(order)

    async def update_shipment(
        self,
        order_id: uuid.UUID,
        body: ShipmentUpdateRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminOrderDetail:
        """Edit courier/tracking on an already-shipped order.

        Lets admin correct a typo or swap couriers without forcing a
        status round-trip. Doesn't change `shipped_at` — that's stamped
        once by `mark_shipped`.
        """
        order = await self._require_order(order_id)
        shipment = await self._get_or_create_shipment(order)
        changes: dict[str, dict[str, Any]] = {}
        for field in ("courier_name", "tracking_id", "tracking_url"):
            new_value = getattr(body, field)
            if new_value is not None and new_value != getattr(shipment, field):
                changes[field] = {
                    "from": getattr(shipment, field),
                    "to": new_value,
                }
                setattr(shipment, field, new_value)
        if not changes:
            return await self._to_detail_loaded(order)

        await self.session.flush()
        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="order",
            entity_id=order.id,
            actor_user_id=actor_user_id,
            summary=f"Order {order.order_number} shipment updated",
            metadata={"type": "shipment_updated", "changed": changes},
            request_id=request_id,
        )
        return await self._to_detail_loaded(order)

    async def add_note(
        self,
        order_id: uuid.UUID,
        body: AddOrderNoteRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> OrderTimelineEvent:
        """Admin notes go on the audit log timeline (entity_type='order',
        metadata.type='admin_note') — preserves the customer's `order.notes`
        for the gift-wrap-instructions kind of message.
        """
        order = await self._require_order(order_id)
        row = await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="order",
            entity_id=order.id,
            actor_user_id=actor_user_id,
            summary=f"Admin note on {order.order_number}: {body.note[:80]}",
            metadata={"type": "admin_note", "note": body.note},
            request_id=request_id,
        )
        return OrderTimelineEvent(
            at=row.created_at,
            type=OrderTimelineEventType.NOTE_ADDED,
            actor_user_id=actor_user_id,
            summary=row.summary or "Admin note",
            metadata={"note": body.note},
        )

    # ======================================================================
    # Timeline assembly
    # ======================================================================

    async def build_timeline(self, order: Order) -> list[OrderTimelineEvent]:
        events: list[OrderTimelineEvent] = []

        # 1) Order timestamp columns — the canonical lifecycle markers.
        placed_at = order.placed_at or order.created_at
        events.append(
            OrderTimelineEvent(
                at=placed_at,
                type=OrderTimelineEventType.PLACED,
                actor_user_id=None,
                summary=f"Order {order.order_number} placed",
                metadata={"payment_method": order.payment_method.value},
            )
        )
        if order.packed_at:
            events.append(
                OrderTimelineEvent(
                    at=order.packed_at,
                    type=OrderTimelineEventType.PACKED,
                    actor_user_id=None,
                    summary="Marked packed",
                    metadata={},
                )
            )
        if order.shipped_at:
            events.append(
                OrderTimelineEvent(
                    at=order.shipped_at,
                    type=OrderTimelineEventType.SHIPPED,
                    actor_user_id=None,
                    summary="Marked shipped",
                    metadata={},
                )
            )
        if order.delivered_at:
            events.append(
                OrderTimelineEvent(
                    at=order.delivered_at,
                    type=OrderTimelineEventType.DELIVERED,
                    actor_user_id=None,
                    summary="Marked delivered",
                    metadata={},
                )
            )
        if order.cancelled_at:
            events.append(
                OrderTimelineEvent(
                    at=order.cancelled_at,
                    type=OrderTimelineEventType.CANCELLED,
                    actor_user_id=None,
                    summary="Order cancelled",
                    metadata={},
                )
            )

        # 2) Payments — captured/failed events.
        for p in order.payments:
            if p.status is RazorpayPaymentStatus.CAPTURED and p.captured_at:
                events.append(
                    OrderTimelineEvent(
                        at=p.captured_at,
                        type=OrderTimelineEventType.PAYMENT_CAPTURED,
                        actor_user_id=None,
                        summary=f"Razorpay payment captured ({p.amount} {p.currency})",
                        metadata={
                            "razorpay_payment_id": p.razorpay_payment_id,
                            "method": p.method,
                            "amount": str(p.amount),
                        },
                    )
                )
            elif p.status is RazorpayPaymentStatus.FAILED:
                events.append(
                    OrderTimelineEvent(
                        at=p.created_at,
                        type=OrderTimelineEventType.PAYMENT_FAILED,
                        actor_user_id=None,
                        summary=f"Razorpay payment failed: {p.failure_reason or 'unknown'}",
                        metadata={
                            "razorpay_payment_id": p.razorpay_payment_id,
                            "failure_reason": p.failure_reason,
                        },
                    )
                )

        # 3) Audit-log events: admin notes, shipment updates, COD-paid.
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.entity_type == "order",
                AuditLog.entity_id == str(order.id),
            )
            .order_by(AuditLog.created_at.asc())
        )
        for row in (await self.session.execute(stmt)).scalars().all():
            event = self._audit_to_timeline(row)
            if event is not None:
                events.append(event)

        events.sort(key=lambda e: e.at, reverse=True)
        return events

    # ======================================================================
    # Internal helpers
    # ======================================================================

    async def _require_order(self, order_id: uuid.UUID) -> Order:
        order = await self.orders.get_admin(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        return order

    @staticmethod
    def _assert_transition(
        current: OrderStatus, target: OrderStatus
    ) -> None:
        """Validate the from→to move per the state machine.

        Idempotent (current == target) is handled BEFORE this method runs
        in the public path; this method only catches truly invalid moves.
        """
        valid: dict[OrderStatus, set[OrderStatus]] = {
            OrderStatus.PLACED: {
                OrderStatus.PACKED,
                OrderStatus.CANCELLED,
            },
            OrderStatus.PACKED: {
                OrderStatus.SHIPPED,
                OrderStatus.CANCELLED,
            },
            OrderStatus.SHIPPED: {
                OrderStatus.DELIVERED,
                OrderStatus.CANCELLED,
            },
            OrderStatus.DELIVERED: set(),  # terminal — Phase 4 returns flow
            OrderStatus.CANCELLED: set(),  # terminal
        }
        if current in _TERMINAL_STATES:
            raise ConflictError(
                f"Order is in terminal state '{current.value}'",
                code="invalid_status_transition",
                extra={"current": current.value, "target": target.value},
            )
        if target not in valid.get(current, set()):
            raise ConflictError(
                f"Invalid status transition: {current.value} → {target.value}",
                code="invalid_status_transition",
                extra={"current": current.value, "target": target.value},
            )

    async def _get_or_create_shipment(self, order: Order) -> Shipment:
        if order.shipment is not None:
            return order.shipment
        shipment = Shipment(order_id=order.id)
        self.session.add(shipment)
        await self.session.flush()
        # Refresh the relationship cache so callers can keep using
        # `order.shipment` without triggering a lazy load.
        order.shipment = shipment
        return shipment

    async def _audit_transition(
        self,
        order: Order,
        before: OrderStatus,
        actor_user_id: uuid.UUID,
        request_id: str | None,
        *,
        verb: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> None:
        meta: dict[str, Any] = {
            "from": before.value,
            "to": order.status.value,
        }
        if extra_meta:
            meta.update(extra_meta)
        await self.audit.record(
            action=AuditAction.STATUS_CHANGED,
            entity_type="order",
            entity_id=order.id,
            actor_user_id=actor_user_id,
            summary=f"Order {order.order_number} {verb}",
            metadata=meta,
            request_id=request_id,
        )
        log.info(
            "admin_order_transition",
            order_number=order.order_number,
            from_status=before.value,
            to_status=order.status.value,
            actor=str(actor_user_id),
        )

    @staticmethod
    def _audit_to_timeline(row: AuditLog) -> OrderTimelineEvent | None:
        """Map an audit_logs row to a timeline event when it's
        admin-introduced (note / shipment update / COD-paid).

        Status-transition audit rows are deliberately skipped here —
        they'd duplicate the canonical timestamp-column events.
        """
        meta = row.metadata_ or {}
        event_type: OrderTimelineEventType | None = None
        if meta.get("type") == "admin_note":
            event_type = OrderTimelineEventType.NOTE_ADDED
        elif meta.get("type") == "shipment_updated":
            event_type = OrderTimelineEventType.SHIPMENT_UPDATED
        elif (
            row.action is AuditAction.PAYMENT_CAPTURED
            and meta.get("payment_method") == "cod"
        ):
            event_type = OrderTimelineEventType.COD_PAID
        if event_type is None:
            return None
        return OrderTimelineEvent(
            at=row.created_at,
            type=event_type,
            actor_user_id=row.actor_user_id,
            summary=row.summary or event_type.value,
            metadata=meta,
        )

    # ---------- Response mapping --------------------------------------------

    async def _to_detail_loaded(self, order: Order) -> AdminOrderDetail:
        # `order` is freshly mutated and bound to session — relationships
        # are already loaded (we use selectinload in get_admin). Refresh
        # for server-managed `updated_at` to surface in the response.
        await self.session.refresh(order)
        timeline = await self.build_timeline(order)
        return self._to_detail(order, timeline)

    @staticmethod
    def _to_detail(
        order: Order, timeline: Sequence[OrderTimelineEvent]
    ) -> AdminOrderDetail:
        items = [
            AdminOrderItemRead(
                id=i.id,
                product_id=i.product_id,
                variant_id=i.variant_id,
                product_name=i.product_name,
                variant_label=i.variant_label,
                sku=i.sku,
                primary_image_url=i.primary_image_url,
                unit_price=i.unit_price,
                quantity=i.quantity,
                line_total=i.unit_price * i.quantity,
            )
            for i in order.items
        ]
        payments = [
            AdminPaymentRead(
                id=p.id,
                razorpay_order_id=p.razorpay_order_id,
                razorpay_payment_id=p.razorpay_payment_id,
                amount=p.amount,
                currency=p.currency,
                status=p.status.value,
                method=p.method,
                failure_reason=p.failure_reason,
                captured_at=p.captured_at,
                created_at=p.created_at,
            )
            for p in order.payments
        ]
        shipment: AdminShipmentRead | None = None
        if order.shipment is not None:
            shipment = AdminShipmentRead.model_validate(order.shipment)
        return AdminOrderDetail(
            id=order.id,
            order_number=order.order_number,
            user_id=order.user_id,
            customer_name=order.customer_name,
            email=order.email,
            phone=order.phone,
            status=order.status,
            payment_status=order.payment_status,
            payment_method=order.payment_method,
            subtotal=order.subtotal,
            shipping_amount=order.shipping_amount,
            tax_amount=order.tax_amount,
            discount_amount=order.discount_amount,
            total=order.total,
            currency=order.currency,
            coupon_id=order.coupon_id,
            shipping_address=order.shipping_address,
            billing_address=order.billing_address,
            notes=order.notes,
            placed_at=order.placed_at,
            packed_at=order.packed_at,
            shipped_at=order.shipped_at,
            delivered_at=order.delivered_at,
            cancelled_at=order.cancelled_at,
            created_at=order.created_at,
            updated_at=order.updated_at,
            items=items,
            payments=payments,
            shipment=shipment,
            timeline=list(timeline),
        )

    @staticmethod
    def list_allowed_targets(current: OrderStatus) -> Sequence[OrderStatus]:
        """For introspection / tests — what moves are valid from `current`."""
        matrix = {
            OrderStatus.PLACED: [OrderStatus.PACKED, OrderStatus.CANCELLED],
            OrderStatus.PACKED: [OrderStatus.SHIPPED, OrderStatus.CANCELLED],
            OrderStatus.SHIPPED: [OrderStatus.DELIVERED, OrderStatus.CANCELLED],
            OrderStatus.DELIVERED: [],
            OrderStatus.CANCELLED: [],
        }
        return matrix.get(current, [])


# Reserved import for any future deserialiser that needs it.
_ = Payment
