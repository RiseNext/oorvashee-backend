"""Wishlist Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class WishlistItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product_slug: str
    product_name: str
    base_price: Decimal
    mrp: Decimal | None
    primary_image_url: str | None
    available: bool
    added_at: datetime


class WishlistRead(BaseModel):
    items: list[WishlistItemRead]
    count: int


class MoveToCartRequest(BaseModel):
    variant_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=99)
    remove_from_wishlist: bool = True
