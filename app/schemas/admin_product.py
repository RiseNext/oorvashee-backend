"""Admin-facing product schemas.

Kept SEPARATE from `app/schemas/product.py` (which is the public catalog
shape) because admin needs:
  - all status values (DRAFT, ARCHIVED) and audit timestamps
  - full variant + image management payloads
  - explicit, narrow Update bodies (mass-assignment protection)

Critical invariants enforced at the schema layer:
  - `slug` is generated server-side at creation; the Update schema does
    NOT expose it. Even if a buggy admin tool sends `"slug": "..."` in a
    PATCH body, Pydantic strips it (`extra="forbid"` would error; we
    `extra="ignore"` because trusted-but-evolving admin UI clients are
    less hostile than public clients).
  - `status` transitions go through dedicated endpoints, not the Update
    body. Update DOES NOT accept `status`.
  - `search_vector` is DB-managed; never appears here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ProductStatus

# ---------------------------------------------------------------------------
# Variant payloads
# ---------------------------------------------------------------------------


class AdminVariantCreate(BaseModel):
    """One variant. Inventory row is created alongside with `initial_stock`."""

    model_config = ConfigDict(extra="ignore")

    sku: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=80)
    fabric: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=40)
    price_override: Decimal | None = Field(
        default=None, ge=Decimal("0"), description="Falls back to product.base_price when null"
    )
    weight_grams: int | None = Field(default=None, ge=0, le=10_000)
    is_default: bool = False
    is_active: bool = True
    initial_stock: int = Field(default=0, ge=0, le=100_000)
    low_stock_threshold: int = Field(default=2, ge=0, le=10_000)


class AdminVariantUpdate(BaseModel):
    """Partial update. All fields optional; `null` is meaningful (clears)."""

    model_config = ConfigDict(extra="ignore")

    color: str | None = Field(default=None, max_length=80)
    fabric: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=40)
    price_override: Decimal | None = Field(default=None, ge=Decimal("0"))
    weight_grams: int | None = Field(default=None, ge=0, le=10_000)
    is_default: bool | None = None
    is_active: bool | None = None


class AdminVariantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    color: str | None
    fabric: str | None
    size: str | None
    price_override: Decimal | None
    weight_grams: int | None
    is_default: bool
    is_active: bool
    stock: int = Field(description="Current inventory stock for this variant.")
    reserved: int = Field(description="Stock currently reserved by in-flight checkouts.")
    low_stock_threshold: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Image payload (admin read-side only; attach/delete already live in
# admin/media.py via the Cloudinary signed-upload flow)
# ---------------------------------------------------------------------------


class AdminImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cloudinary_public_id: str
    url: str
    alt_text: str | None
    width: int | None
    height: int | None
    format: str | None
    bytes: int | None
    position: int
    is_primary: bool


# ---------------------------------------------------------------------------
# Product payloads
# ---------------------------------------------------------------------------


class AdminProductCreate(BaseModel):
    """Initial product. Slug is server-derived from name; can be overridden
    but only at creation time (PRD §7.2 makes it immutable after that)."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=2, max_length=255)
    slug: str | None = Field(
        default=None,
        min_length=2,
        max_length=220,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Optional override. Lowercase letters, digits, hyphens. "
        "Auto-generated from name if omitted.",
    )
    short_description: str | None = Field(default=None, max_length=500)
    description: str | None = None
    base_price: Decimal = Field(gt=Decimal("0"), le=Decimal("9999999.99"))
    mrp: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999.99"))
    currency: str = Field(default="INR", min_length=3, max_length=3)
    tags: list[str] = Field(default_factory=list, max_length=50)
    featured: bool = False
    is_bestseller: bool = False
    is_new: bool = False
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = None
    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    variants: list[AdminVariantCreate] = Field(default_factory=list, max_length=50)

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip().lower() for t in value if t and t.strip()]
        # De-dupe preserving first-occurrence order.
        seen: set[str] = set()
        out: list[str] = []
        for t in cleaned:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.upper()


class AdminProductUpdate(BaseModel):
    """Partial update. Slug + status are intentionally NOT here.

    Status transitions go through dedicated endpoints (publish / unpublish
    / archive / unarchive) so business rules around `published_at` and
    audit logging stay in one place.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=2, max_length=255)
    short_description: str | None = Field(default=None, max_length=500)
    description: str | None = None
    base_price: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999.99"))
    mrp: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999.99"))
    tags: list[str] | None = Field(default=None, max_length=50)
    featured: bool | None = None
    is_bestseller: bool | None = None
    is_new: bool | None = None
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = None

    @field_validator("tags")
    @classmethod
    def _normalise_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [t.strip().lower() for t in value if t and t.strip()]
        seen: set[str] = set()
        out: list[str] = []
        for t in cleaned:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out


class AdminCategoryAssignRequest(BaseModel):
    """Replace the product's category set in one shot.

    Replace-semantics (PUT) is simpler than tracking per-category add/remove
    and gives the admin UI a clean form: "the categories selected right now
    ARE the categories." Idempotent under network retry.
    """

    model_config = ConfigDict(extra="ignore")

    category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class AdminProductRead(BaseModel):
    """Full admin view — every field a moderator might need."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    short_description: str | None
    description: str | None
    base_price: Decimal
    mrp: Decimal | None
    currency: str
    status: ProductStatus
    tags: list[str]
    featured: bool
    is_bestseller: bool
    is_new: bool
    seo_title: str | None
    seo_description: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None

    variants: list[AdminVariantRead] = Field(default_factory=list)
    images: list[AdminImageRead] = Field(default_factory=list)
    category_ids: list[uuid.UUID] = Field(default_factory=list)


class AdminProductListItem(BaseModel):
    """Lean shape for the admin product table."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    status: ProductStatus
    base_price: Decimal
    currency: str
    featured: bool
    is_bestseller: bool
    is_new: bool
    primary_image_url: str | None
    total_stock: int
    variant_count: int
    published_at: datetime | None
    updated_at: datetime
