"""CSV bulk-import schemas.

Three row schemas (one per kind) + job tracking shapes. CSV rows go
through Pydantic for per-row validation; rows that fail validation are
recorded in `import_jobs.errors` with their row number + field-level
messages, the rest of the chunk continues.

Boolean parsing is intentionally generous: Excel-exported CSVs use
`TRUE/FALSE/Yes/No/1/0` interchangeably.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ImportKind,
    ImportStatus,
    ProductStatus,
    StockMovementReason,
)

_TRUE_TOKENS = {"true", "t", "yes", "y", "1"}
_FALSE_TOKENS = {"false", "f", "no", "n", "0", ""}


def _parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in _TRUE_TOKENS:
        return True
    if s in _FALSE_TOKENS:
        return False
    raise ValueError(f"expected bool-ish value, got {value!r}")


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").lstrip("₹$").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"expected decimal, got {value!r}") from exc


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"expected integer, got {value!r}") from exc


def _split_tags(value: Any) -> list[str]:
    """Tags are pipe-separated (`|`) to avoid CSV comma collisions."""
    if value is None or str(value).strip() == "":
        return []
    parts = [p.strip().lower() for p in str(value).split("|")]
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


# =============================================================================
# Row schemas (one per import kind)
# =============================================================================


class ProductCsvRow(BaseModel):
    """One product row from a CSV import.

    Required: `name`, `base_price`. Slug is auto-derived from name if absent.
    `status` defaults to `draft` per the same convention as the admin POST.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    slug: str | None = Field(
        default=None,
        max_length=220,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    name: str = Field(min_length=2, max_length=255)
    short_description: str | None = Field(default=None, max_length=500)
    description: str | None = None
    base_price: Decimal = Field(gt=Decimal("0"), le=Decimal("9999999.99"))
    mrp: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999.99"))
    currency: str = "INR"
    tags: list[str] = Field(default_factory=list, max_length=50)
    featured: bool = False
    is_bestseller: bool = False
    is_new: bool = False
    status: ProductStatus = ProductStatus.DRAFT
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = None

    @field_validator("base_price", "mrp", mode="before")
    @classmethod
    def _coerce_decimal(cls, value: Any) -> Any:
        return _parse_decimal(value) if isinstance(value, str) else value

    @field_validator(
        "featured", "is_bestseller", "is_new", mode="before"
    )
    @classmethod
    def _coerce_bool(cls, value: Any) -> Any:
        return _parse_bool(value) if isinstance(value, str) else value

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: Any) -> Any:
        return _split_tags(value) if isinstance(value, str) else value

    @field_validator("status", mode="before")
    @classmethod
    def _coerce_status(cls, value: Any) -> Any:
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"", "draft"}:
                return ProductStatus.DRAFT
            if v == "published":
                return ProductStatus.PUBLISHED
        return value


class VariantCsvRow(BaseModel):
    """One variant row. References its parent product by `product_slug`.

    `initial_stock` seeds the inventory row alongside the new variant —
    same atomic semantics as `AdminProductService.add_variant`.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    product_slug: str = Field(min_length=2, max_length=220)
    sku: str = Field(min_length=1, max_length=100)
    color: str | None = Field(default=None, max_length=80)
    fabric: str | None = Field(default=None, max_length=80)
    size: str | None = Field(default=None, max_length=40)
    price_override: Decimal | None = Field(default=None, ge=Decimal("0"))
    weight_grams: int | None = Field(default=None, ge=0, le=10_000)
    is_default: bool = False
    is_active: bool = True
    initial_stock: int = Field(default=0, ge=0, le=100_000)
    low_stock_threshold: int = Field(default=2, ge=0, le=10_000)

    @field_validator("price_override", mode="before")
    @classmethod
    def _coerce_decimal(cls, value: Any) -> Any:
        return _parse_decimal(value) if isinstance(value, str) else value

    @field_validator("weight_grams", "initial_stock", "low_stock_threshold", mode="before")
    @classmethod
    def _coerce_int(cls, value: Any) -> Any:
        return _parse_int(value) if isinstance(value, str) else value

    @field_validator("is_default", "is_active", mode="before")
    @classmethod
    def _coerce_bool(cls, value: Any) -> Any:
        return _parse_bool(value) if isinstance(value, str) else value


class InventoryCsvRow(BaseModel):
    """One inventory adjustment row. Identifies the variant by SKU.

    For retry-safety, prefer `mode=set` over increment/decrement — the
    set operation is idempotent under re-runs of the same CSV. Increment
    on retry double-applies; admin templates documentation calls this out.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    sku: str = Field(min_length=1, max_length=100)
    mode: str = Field(pattern=r"^(set|increment|decrement)$")
    value: int = Field(ge=0, le=1_000_000)
    reason: StockMovementReason = StockMovementReason.MANUAL_ADJUSTMENT
    note: str | None = Field(default=None, max_length=500)

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_int(cls, value: Any) -> Any:
        return _parse_int(value) if isinstance(value, str) else value

    @field_validator("reason", mode="before")
    @classmethod
    def _coerce_reason(cls, value: Any) -> Any:
        if isinstance(value, str):
            v = value.strip().lower()
            if v == "" or v == "manual_adjustment":
                return StockMovementReason.MANUAL_ADJUSTMENT
            if v == "restock":
                return StockMovementReason.RESTOCK
        return value


# =============================================================================
# Job tracking shapes
# =============================================================================


class ImportRowError(BaseModel):
    """Single failed row's diagnostics."""

    model_config = ConfigDict(from_attributes=True)

    row_number: int = Field(description="1-indexed CSV row (data rows; header is row 1).")
    errors: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(
        default_factory=dict,
        description="Subset of the original row — capped to first 10 fields for size.",
    )


class ImportJobRead(BaseModel):
    """Status + progress + recent errors for an admin poll."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ImportKind
    status: ImportStatus
    filename: str | None
    dry_run: bool
    rows_total: int | None
    rows_processed: int
    rows_succeeded: int
    rows_failed: int
    errors: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None
    completed_at: datetime | None
    failure_reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ImportJobListItem(BaseModel):
    """Lean shape for the imports table."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ImportKind
    status: ImportStatus
    filename: str | None
    dry_run: bool
    rows_total: int | None
    rows_processed: int
    rows_succeeded: int
    rows_failed: int
    created_at: datetime
    completed_at: datetime | None


# =============================================================================
# Template kind for CSV download
# =============================================================================


class TemplateKind(StrEnum):
    PRODUCT = "product"
    VARIANT = "variant"
    INVENTORY = "inventory"
