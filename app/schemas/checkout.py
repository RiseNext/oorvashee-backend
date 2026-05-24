"""Checkout + order Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
)

# ---------- Shared address shape ----------


class CheckoutAddress(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=6, max_length=32)
    line1: str = Field(min_length=1, max_length=255)
    line2: str | None = Field(default=None, max_length=255)
    city: str = Field(min_length=1, max_length=120)
    state: str = Field(min_length=1, max_length=120)
    postal_code: str = Field(min_length=4, max_length=16)
    country: str = Field(default="IN", min_length=2, max_length=2)


class CheckoutCustomer(BaseModel):
    email: EmailStr
    phone: str = Field(min_length=6, max_length=32)
    full_name: str = Field(min_length=1, max_length=255)


# ---------- Quote (server-side total recomputation) ----------


class QuoteLine(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)


class CheckoutQuoteRequest(BaseModel):
    items: list[QuoteLine] = Field(min_length=1, max_length=50)
    shipping_postal_code: str | None = Field(default=None, max_length=16)
    coupon_code: str | None = Field(default=None, max_length=64)


class QuoteLineRead(BaseModel):
    variant_id: uuid.UUID
    product_name: str
    variant_label: str | None
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    available: bool
    available_stock: int


class CheckoutQuoteRead(BaseModel):
    items: list[QuoteLineRead]
    subtotal: Decimal
    shipping_amount: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total: Decimal
    currency: str = "INR"
    has_unavailable_items: bool


# ---------- Place order ----------


class PlaceOrderRequest(BaseModel):
    """Server-authoritative order placement.

    `items` is required even for authenticated users: the frontend passes
    its current view of the cart so the server can detect (and reject)
    out-of-sync states.

    Use `Idempotency-Key` HTTP header — POSTing the same key replays the
    cached response and never double-charges.
    """

    items: list[QuoteLine] = Field(min_length=1, max_length=50)
    customer: CheckoutCustomer
    shipping_address: CheckoutAddress
    billing_address: CheckoutAddress | None = None
    payment_method: PaymentMethod
    coupon_code: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=1000)


class RazorpayHandoff(BaseModel):
    """Frontend invokes the Razorpay Checkout widget with these values."""

    razorpay_order_id: str
    razorpay_key_id: str
    amount_paise: int
    currency: str = "INR"


class PlaceOrderRead(BaseModel):
    order_number: str
    status: OrderStatus
    payment_status: PaymentStatus
    payment_method: PaymentMethod
    total: Decimal
    currency: str = "INR"
    payment: RazorpayHandoff | None = Field(
        default=None,
        description="Present only when payment_method == razorpay.",
    )


# ---------- Payment verify ----------


class VerifyPaymentRequest(BaseModel):
    order_number: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ---------- Order read ----------


class OrderItemRead(BaseModel):
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


class ShipmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    courier_name: str | None
    tracking_id: str | None
    tracking_url: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: OrderStatus
    payment_status: PaymentStatus
    payment_method: PaymentMethod
    customer_name: str
    email: EmailStr
    phone: str
    items: list[OrderItemRead]
    subtotal: Decimal
    shipping_amount: Decimal
    tax_amount: Decimal
    discount_amount: Decimal
    total: Decimal
    currency: str
    shipping_address: dict
    billing_address: dict | None
    notes: str | None
    placed_at: datetime | None
    packed_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    shipment: ShipmentRead | None
