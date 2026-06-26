"""CourierService — delivery-partner operations on a STRICTLY SLIM surface.

The courier sees only delivery-relevant fields (name, phone, address, order
number, fulfillment status, AWB). It never touches payment, totals, email,
billing, or items. The actual write (AWB → shipped) REUSES the proven admin
state machine (`AdminOrderService.mark_shipped`) so shipping behavior, transition
guards, and the audit-log timeline are identical to an admin shipping the order
— only the (slim) API surface differs.

Dakia seam: the AWB is STORED only. Live carrier tracking from the AWB will be
wired in here later when Dakia's API docs arrive — see the TODO in `set_awb`.
No external courier API is called today.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.integrations.daakia_client import DaakiaClient
from app.models.enums import OrderStatus, PaymentStatus
from app.models.order import Order
from app.schemas.admin_order import ShipmentUpdateRequest
from app.schemas.courier import (
    CourierAddress,
    CourierOrder,
    CourierVendor,
    SetAwbRequest,
)
from app.services.admin_order_service import AdminOrderService
from app.services.base import BaseService

# Paid orders a courier handles: placed/packed/shipped — i.e. not yet delivered
# or cancelled. (placed = paid but not yet marked ready; packed = Ready ✓.)
_DISPATCH_STATUSES: tuple[OrderStatus, ...] = (
    OrderStatus.PLACED,
    OrderStatus.PACKED,
    OrderStatus.SHIPPED,
)
_DISPATCH_LIMIT = 200


class CourierService(BaseService):
    async def list_dispatch_orders(self) -> list[CourierOrder]:
        """Paid orders awaiting / at dispatch, newest first. Slim payload only."""
        stmt = (
            select(Order)
            .where(
                Order.payment_status == PaymentStatus.PAID,
                Order.status.in_(_DISPATCH_STATUSES),
            )
            .options(selectinload(Order.shipment))
            .order_by(Order.created_at.desc())
            .limit(_DISPATCH_LIMIT)
        )
        orders: Sequence[Order] = (await self.session.execute(stmt)).scalars().all()
        return [self._to_slim(o) for o in orders]

    async def set_awb(
        self,
        order_number: str,
        body: SetAwbRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> CourierOrder:
        order = await self._get_paid_order(order_number)

        if order.status is OrderStatus.SHIPPED:
            raise ConflictError(
                "This order has already been dispatched.",
                code="already_dispatched",
            )
        if order.status is not OrderStatus.PACKED:
            # Admin must mark the order Ready for Dispatch (packed) first.
            raise ConflictError(
                "This order is not ready for dispatch yet.",
                code="order_not_ready",
            )

        # Reuse the admin packed→shipped transition for the AWB + status + audit;
        # the carrier name shown is the Daakia vendor the courier selected.
        # NOTE: we do NOT book/cancel with Daakia — the courier books outside our
        # system; we only store awb + vendor so the customer page can track live.
        courier_label = body.vendor_name or f"Vendor {body.vendor_id}"
        await AdminOrderService(self.session).mark_shipped(
            order.id,
            ShipmentUpdateRequest(
                courier_name=courier_label,
                tracking_id=body.awb,
            ),
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        # Persist the Daakia vendor on the shipment — required (with the AWB) for
        # the live track-shipment call. mark_shipped guarantees a shipment row.
        refreshed = await self._get_paid_order(order_number)
        if refreshed.shipment is not None:
            refreshed.shipment.courier_vendor_id = body.vendor_id
            refreshed.shipment.courier_vendor_name = body.vendor_name
            await self.session.flush()
        return self._to_slim(refreshed)

    async def list_vendors(self) -> list[CourierVendor]:
        """Daakia carriers for the AWB dropdown (token + list cached in the client)."""
        vendors = await DaakiaClient(get_settings()).list_vendors()
        return [
            CourierVendor(vendor_id=v.vendor_id, vendor_name=v.vendor_name)
            for v in vendors
        ]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_paid_order(self, order_number: str) -> Order:
        stmt = (
            select(Order)
            .where(Order.order_number == order_number)
            .options(selectinload(Order.shipment))
        )
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order not found", code="order_not_found")
        # Defence-in-depth: a courier may only ever act on PAID orders. The list
        # query already filters to PAID; this guards the by-number AWB write too.
        if order.payment_status is not PaymentStatus.PAID:
            raise PermissionDeniedError(
                "Order is not available for dispatch.",
                code="order_not_dispatchable",
            )
        return order

    @staticmethod
    def _to_slim(order: Order) -> CourierOrder:
        shipment = order.shipment
        return CourierOrder(
            order_number=order.order_number,
            customer_name=order.customer_name,
            phone=order.phone,
            delivery_address=CourierAddress.model_validate(order.shipping_address or {}),
            status=order.status.value,
            is_ready=order.status is OrderStatus.PACKED,
            awb=shipment.tracking_id if shipment else None,
            courier_name=shipment.courier_name if shipment else None,
            vendor_id=shipment.courier_vendor_id if shipment else None,
        )
