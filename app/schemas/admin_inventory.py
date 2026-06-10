"""Admin inventory schemas.

Design notes:
- `AdjustmentMode` is a discriminated input: `set` carries an absolute
  target stock; `increment`/`decrement` carry a positive delta.
- Reasons are restricted at the schema layer to the values an admin may
  legitimately pick. System-only reasons (`order_placed`, `csv_import`,
  etc.) are filtered server-side regardless — defense in depth.
- Response shapes are LEAN by default. Movement timelines paginate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ReservationStatus, StockMovementReason


class AdjustmentMode(StrEnum):
    INCREMENT = "increment"
    DECREMENT = "decrement"
    SET = "set"


# Reasons an admin may pick. System reasons (`order_placed`,
# `order_cancelled`, `csv_import`) are blocked even if sent by a malformed
# client because the service re-validates against this whitelist.
_ADMIN_PICKABLE_REASONS: set[StockMovementReason] = {
    StockMovementReason.MANUAL_ADJUSTMENT,
    StockMovementReason.RESTOCK,
}


# ---------- Adjustment request / response ---------------------------------


class StockAdjustmentRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: AdjustmentMode
    value: int = Field(
        ge=0,
        le=1_000_000,
        description=(
            "For INCREMENT/DECREMENT: positive delta. "
            "For SET: absolute target stock."
        ),
    )
    reason: StockMovementReason = Field(
        description="Picked from {manual_adjustment, restock}. "
        "Service rejects anything else as `reason_not_allowed`.",
    )
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_mode_value(self) -> StockAdjustmentRequest:
        if (
            self.mode in (AdjustmentMode.INCREMENT, AdjustmentMode.DECREMENT)
            and self.value <= 0
        ):
            raise ValueError(
                f"{self.mode.value} requires value > 0; use SET for absolute 0"
            )
        if self.reason not in _ADMIN_PICKABLE_REASONS:
            raise ValueError(
                f"reason '{self.reason.value}' is not admin-pickable; "
                f"choose one of {sorted(r.value for r in _ADMIN_PICKABLE_REASONS)}"
            )
        return self


class StockAdjustmentResult(BaseModel):
    """What the adjust endpoint returns — current state + movement record."""

    model_config = ConfigDict(from_attributes=True)

    variant_id: uuid.UUID
    sku: str
    stock_before: int
    stock_after: int
    reserved: int
    delta: int
    movement_id: uuid.UUID
    product_status_after: str | None = Field(
        default=None,
        description="Set only when the adjustment auto-flipped the parent "
        "product between PUBLISHED ↔ UNAVAILABLE.",
    )


# ---------- Threshold update ----------------------------------------------


class ThresholdUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    low_stock_threshold: int = Field(ge=0, le=10_000)


# ---------- List / detail shapes ------------------------------------------


class InventoryListItem(BaseModel):
    """Lean shape for the admin inventory table."""

    model_config = ConfigDict(from_attributes=True)

    variant_id: uuid.UUID
    sku: str
    product_id: uuid.UUID
    product_name: str
    product_slug: str
    variant_label: str | None = Field(
        default=None,
        description="`color / fabric / size` joined for the admin row label.",
    )
    stock: int = Field(description="Physical stock_on_hand (== inventory.stock).")
    reserved: int = Field(
        description="DERIVED — SUM of active (RESERVED + PAYMENT_PROCESSING, "
        "non-expired) reservations for this variant."
    )
    available: int = Field(description="`stock - reserved` (Model A, never negative).")
    sold: int = Field(description="Cumulative units sold (forward-only analytics).")
    low_stock_threshold: int
    is_low_stock: bool
    is_out_of_stock: bool
    is_default_variant: bool
    price: Decimal = Field(description="`variant.price_override` ?? `product.base_price`.")


class MovementItem(BaseModel):
    """One row of the stock-movement audit timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    sku: str
    product_name: str
    delta: int
    reason: StockMovementReason
    order_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    reference: str | None
    note: str | None
    created_at: datetime


class ReservationCounts(BaseModel):
    """The four admin reservation buckets for one variant."""

    active: int = Field(description="RESERVED + PAYMENT_PROCESSING.")
    expired: int
    completed: int
    cancelled: int


class InventoryDetail(BaseModel):
    """Variant-detail view: current state + recent movements (last 20)."""

    model_config = ConfigDict(from_attributes=True)

    variant_id: uuid.UUID
    sku: str
    product_id: uuid.UUID
    product_name: str
    product_slug: str
    color: str | None
    fabric: str | None
    size: str | None
    stock: int
    reserved: int = Field(description="DERIVED — SUM of active reservations.")
    available: int = Field(description="`stock - reserved` (Model A).")
    sold: int = Field(description="Cumulative units sold (forward-only analytics).")
    low_stock_threshold: int
    is_low_stock: bool
    is_out_of_stock: bool
    is_default_variant: bool
    price: Decimal
    reservation_counts: ReservationCounts
    recent_movements: list[MovementItem]


# ---------- Reservation admin list ----------------------------------------


class ReservationAdminItem(BaseModel):
    """One reservation row for the admin Active/Expired/Completed/Cancelled lists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    checkout_session_id: uuid.UUID
    variant_id: uuid.UUID
    sku: str
    product_name: str
    user_id: uuid.UUID
    quantity: int
    status: ReservationStatus
    bucket: str = Field(description="active | expired | completed | cancelled.")
    is_expired: bool = Field(
        description="True when an active row is already past expires_at but the "
        "cron hasn't swept it yet."
    )
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


# ---------- Health summary ------------------------------------------------


class InventoryHealthSummary(BaseModel):
    """Dashboard tile — what an admin glances at."""

    model_config = ConfigDict(from_attributes=True)

    active_variants: int
    total_stock_units: int
    total_reserved_units: int = Field(
        description="DERIVED — SUM of all active reservations across active variants."
    )
    available_units: int = Field(
        description="`total_stock_units - total_reserved_units`."
    )
    total_sold_units: int = Field(description="Cumulative units sold (analytics).")
    out_of_stock_variants: int
    low_stock_variants: int
    healthy_variants: int = Field(
        description="`active_variants - out_of_stock - low_stock`."
    )
