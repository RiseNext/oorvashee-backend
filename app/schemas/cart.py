"""Cart Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# ---------- Requests ----------


class AddToCartRequest(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=99)


class UpdateCartItemRequest(BaseModel):
    quantity: int = Field(ge=1, le=99)


class MergeCartLine(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)


class MergeCartRequest(BaseModel):
    items: list[MergeCartLine] = Field(default_factory=list, max_length=50)


# ---------- Responses ----------


class CartItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    variant_id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_name: str
    variant_label: str | None
    image_url: str | None
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    available: bool
    max_quantity: int = Field(
        description="Current stock available (informational; checkout re-validates under lock)."
    )


class CartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None = Field(
        default=None, description="Null for an empty cart that hasn't been materialised yet."
    )
    items: list[CartItemRead]
    item_count: int
    distinct_count: int
    subtotal: Decimal
    currency: str = "INR"
    updated_at: datetime | None = None
    has_unavailable_items: bool = False
