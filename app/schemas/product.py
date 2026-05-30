"""Product Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProductStatus
from app.schemas.category import CategorySummary

# ---------- Variant ----------


class VariantSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    color: str | None = None
    fabric: str | None = None
    size: str | None = None
    price: Decimal
    available: bool
    stock: int | None = Field(
        default=None, description="Exposed only when caller has admin role."
    )


# ---------- Image ----------


class ImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    alt_text: str | None
    position: int
    is_primary: bool


# ---------- Product list item ----------


class ProductListItem(BaseModel):
    """Lean shape for catalog grid."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    base_price: Decimal
    mrp: Decimal | None
    currency: str
    primary_image_url: str | None
    available: bool
    featured: bool
    is_bestseller: bool
    is_new: bool


# ---------- Product detail ----------


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    # Admin-managed product code (≠ variant SKU). Optional; storefront PDP hides
    # the row when null. Persisted on `products.code`.
    code: str | None = None
    description: str | None
    short_description: str | None
    base_price: Decimal
    mrp: Decimal | None
    currency: str
    status: ProductStatus
    available: bool
    tags: list[str]
    featured: bool
    is_bestseller: bool
    is_new: bool
    seo_title: str | None
    seo_description: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    images: list[ImageRead]
    variants: list[VariantSummary]
    categories: list[CategorySummary]


# ---------- Filters ----------


class ProductSort(BaseModel):
    """Validated `?sort=` query value."""

    field: str = Field(default="new", pattern=r"^(new|price_asc|price_desc|bestseller|featured)$")
