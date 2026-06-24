"""Courier (delivery-partner) API schemas — DELIBERATELY SLIM.

A courier sees ONLY what is needed to deliver a parcel: consignee name, phone,
delivery address, order number, fulfillment status, and the AWB/tracking number.
It must NEVER see payment amounts, totals/subtotals, customer email, billing
info, or item/pricing data.

These models are the `response_model` on every `/courier` endpoint, so the API
can only ever emit these fields — even if a service accidentally returned a
richer object, FastAPI filters the response to this shape. This is the slim,
PII-minimised payload the brief requires.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CourierAddress(BaseModel):
    """Delivery address — only postal fields, no internal/extra keys.

    `extra="ignore"` means building this from the order's `shipping_address`
    JSONB drops any key not listed here, so nothing unexpected can leak.
    """

    model_config = ConfigDict(extra="ignore")

    recipient_name: str | None = None
    phone: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class CourierOrder(BaseModel):
    """A single dispatch row. No email / totals / payment / billing / items."""

    order_number: str
    customer_name: str
    phone: str
    delivery_address: CourierAddress
    # Fulfillment lifecycle only (placed | packed | shipped) — NOT payment state.
    status: str
    # True when the admin has marked it Ready for Dispatch (status == packed).
    is_ready: bool
    # Current AWB / tracking number on the shipment, once dispatched.
    awb: str | None = None
    courier_name: str | None = None


class SetAwbRequest(BaseModel):
    awb: str = Field(
        min_length=1,
        max_length=120,
        description="Air waybill / tracking number for this parcel.",
    )
    # Carrier label; defaults to the onboarding courier. Stored only for now.
    courier_name: str = Field(default="Dakia", max_length=120)
