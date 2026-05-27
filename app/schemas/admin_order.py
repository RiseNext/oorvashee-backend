"""Admin order schemas.

Order workflows are state-machine driven — admins use dedicated lifecycle
endpoints (`/packed`, `/shipped`, `/delivered`, `/cancel`), not a generic
PATCH. This keeps transition rules, audit logging, and side effects
(restock on cancel, COD auto-paid on delivery, refund flag) in one place.

`payment_status` is NEVER admin-mutable for Razorpay orders — payment
flows are owned by the webhook + verify path. For COD, admins can flip
payment_status via `mark_cod_paid()` only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus

# ---------- Filters (shared with the list endpoint) -----------------------


class OrderListSort(StrEnum):
    """Stable ordering options for the admin orders table."""

    NEWEST = "newest"
    OLDEST = "oldest"
    TOTAL_DESC = "total_desc"
    TOTAL_ASC = "total_asc"


# ---------- List shape ----------------------------------------------------


class AdminOrderListItem(BaseModel):
    """Lean shape for the admin orders table."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    user_id: uuid.UUID | None
    customer_name: str
    email: str
    phone: str
    status: OrderStatus
    payment_status: PaymentStatus
    payment_method: PaymentMethod
    total: Decimal
    currency: str
    item_count: int
    has_shipment: bool
    placed_at: datetime | None
    created_at: datetime


# ---------- Item shape ----------------------------------------------------


class AdminOrderItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    variant_id: uuid.UUID
    product_name: str
    variant_label: str | None
    sku: str | None
    primary_image_url: str | None
    unit_price: Decimal
    quantity: int
    line_total: Decimal


# ---------- Payment shape -------------------------------------------------


class AdminPaymentRead(BaseModel):
    """Subset of `payments` rows admin needs.

    Immutable. Admin sees status + amount + razorpay ids; admin CANNOT
    mutate a captured payment row through this API — refunds in Phase 4
    will write a NEW row, not update the original.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    razorpay_order_id: str
    razorpay_payment_id: str | None
    amount: Decimal
    currency: str
    status: str  # RazorpayPaymentStatus.value
    method: str | None
    failure_reason: str | None
    captured_at: datetime | None
    created_at: datetime


# ---------- Shipment shapes ----------------------------------------------


class AdminShipmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    courier_name: str | None
    tracking_id: str | None
    tracking_url: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None


class ShipmentUpdateRequest(BaseModel):
    """Carried by `mark_shipped()` and `update_shipment()`.

    `mark_shipped()` requires at least courier_name + tracking_id to
    avoid empty shipment rows. `update_shipment()` accepts partial.
    """

    model_config = ConfigDict(extra="ignore")

    courier_name: str | None = Field(default=None, max_length=120)
    tracking_id: str | None = Field(default=None, max_length=120)
    tracking_url: str | None = Field(default=None, max_length=2048)


# ---------- Cancel / note shapes -----------------------------------------


class CancelOrderRequest(BaseModel):
    """Admin must always supply a reason for an audit-grade cancellation log."""

    model_config = ConfigDict(extra="ignore")

    reason: str = Field(min_length=3, max_length=500)
    notify_customer: bool = Field(
        default=False,
        description="When the customer-notification system lands (Phase 4 hook), "
        "true triggers a transactional email. Stored on the audit row meanwhile.",
    )


class AddOrderNoteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    note: str = Field(min_length=1, max_length=2000)


# ---------- Timeline ------------------------------------------------------


class OrderTimelineEventType(StrEnum):
    PLACED = "placed"
    PAYMENT_CAPTURED = "payment_captured"
    PAYMENT_FAILED = "payment_failed"
    PACKED = "packed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    COD_PAID = "cod_paid"
    SHIPMENT_UPDATED = "shipment_updated"
    NOTE_ADDED = "note_added"


class OrderTimelineEvent(BaseModel):
    """One row of the admin order timeline.

    Synthesised by `AdminOrderService.build_timeline()` from three sources:
      - Order timestamp columns (placed_at, packed_at, shipped_at, …)
      - Payments table (captured / failed rows)
      - Audit log rows for `entity_type=order`
    """

    model_config = ConfigDict(from_attributes=True)

    at: datetime
    type: OrderTimelineEventType
    actor_user_id: uuid.UUID | None
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------- Detail --------------------------------------------------------


class AdminOrderDetail(BaseModel):
    """Full admin view of a single order with everything an ops manager needs."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    user_id: uuid.UUID | None
    customer_name: str
    email: str
    phone: str
    status: OrderStatus
    payment_status: PaymentStatus
    payment_method: PaymentMethod
    subtotal: Decimal
    shipping_amount: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total: Decimal
    currency: str
    coupon_id: uuid.UUID | None
    shipping_address: dict
    billing_address: dict | None
    notes: str | None
    placed_at: datetime | None
    packed_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    items: list[AdminOrderItemRead]
    payments: list[AdminPaymentRead]
    shipment: AdminShipmentRead | None
    timeline: list[OrderTimelineEvent]


# ---------- Aggregate / dashboard summary --------------------------------


class OrderStatusSummary(BaseModel):
    """Status-bucket counts for the admin dashboard tile."""

    model_config = ConfigDict(from_attributes=True)

    placed: int
    packed: int
    shipped: int
    delivered: int
    cancelled: int
    pending_payment: int = Field(
        description="Razorpay orders with payment_status=pending."
    )
    cod_pending: int = Field(
        description="COD orders with payment_status=cod_pending."
    )
    awaiting_fulfillment: int = Field(
        description="`status=placed` AND `payment_status in (paid, cod_pending)` — "
        "rows the admin needs to physically pack right now."
    )
    pending_shipment: int = Field(
        description="`status=packed` — packed but not yet handed to courier."
    )
    total_orders: int
    total_revenue_paid: Decimal = Field(
        description="SUM(total) over orders with payment_status=paid. "
        "Excludes COD-pending and refunded."
    )
