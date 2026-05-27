"""Pure-logic tests for AdminProductService — no DB.

Covers:
- Slug generation rules (whitespace, casing, special chars, length cap).
- Status transition validity matrix (admin only sees DRAFT/PUBLISHED/ARCHIVED
  as targets; UNAVAILABLE is system-managed).
- SKU duplicate detection in a create payload.
- Tag normalisation in the schema layer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.models.enums import ProductStatus
from app.schemas.admin_product import (
    AdminProductCreate,
    AdminVariantCreate,
)
from app.services.admin_product_service import AdminProductService, _slugify

# ---------- _slugify ----------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Kanchipuram Bridal Silk", "kanchipuram-bridal-silk"),
        ("  Banarasi  Gold  ", "banarasi-gold"),
        ("Mysore Silk — Pink!", "mysore-silk-pink"),
        ("100% Pure Silk", "100-pure-silk"),
        ("UPPER CASE", "upper-case"),
        ("---", "product"),  # all-separator falls back to placeholder
        ("", "product"),
    ],
)
def test_slugify_normalises(name: str, expected: str) -> None:
    assert _slugify(name) == expected


def test_slugify_caps_at_220_chars() -> None:
    name = "a" * 500
    result = _slugify(name)
    assert len(result) == 220
    assert result == "a" * 220


# ---------- Status-transition validity ----------


@pytest.mark.parametrize(
    "current,target",
    [
        (ProductStatus.DRAFT, ProductStatus.PUBLISHED),
        (ProductStatus.PUBLISHED, ProductStatus.DRAFT),
        (ProductStatus.DRAFT, ProductStatus.ARCHIVED),
        (ProductStatus.PUBLISHED, ProductStatus.ARCHIVED),
        (ProductStatus.UNAVAILABLE, ProductStatus.PUBLISHED),
        (ProductStatus.UNAVAILABLE, ProductStatus.ARCHIVED),
    ],
)
def test_valid_transitions_pass(
    current: ProductStatus, target: ProductStatus
) -> None:
    AdminProductService._assert_transition_allowed(current, target)


def test_unavailable_target_is_rejected() -> None:
    """Admin cannot directly set UNAVAILABLE; it's set by InventoryService."""
    with pytest.raises(ValidationError) as exc:
        AdminProductService._assert_transition_allowed(
            ProductStatus.PUBLISHED, ProductStatus.UNAVAILABLE
        )
    assert exc.value.code == "invalid_status_target"


def test_same_status_transition_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        AdminProductService._assert_transition_allowed(
            ProductStatus.PUBLISHED, ProductStatus.PUBLISHED
        )
    assert exc.value.code == "status_unchanged"


def test_archived_to_published_blocked_directly() -> None:
    """Archive → publish must go via DRAFT (unarchive first)."""
    with pytest.raises(ValidationError) as exc:
        AdminProductService._assert_transition_allowed(
            ProductStatus.ARCHIVED, ProductStatus.PUBLISHED
        )
    assert exc.value.code == "invalid_status_transition"


def test_archived_to_draft_allowed_via_unarchive_path() -> None:
    """Unarchive() pre-validates current==ARCHIVED then targets DRAFT."""
    AdminProductService._assert_transition_allowed(
        ProductStatus.ARCHIVED, ProductStatus.DRAFT
    )


# ---------- SKU duplicate detection ----------


def _variant(sku: str) -> AdminVariantCreate:
    return AdminVariantCreate(sku=sku, initial_stock=1)


def test_duplicate_sku_in_payload_rejected() -> None:
    variants = [_variant("OOR-A-01"), _variant("OOR-B-01"), _variant("OOR-A-01")]
    with pytest.raises(ValidationError) as exc:
        AdminProductService._assert_unique_skus_in_payload(variants)
    assert exc.value.code == "duplicate_sku_in_payload"


def test_unique_skus_in_payload_pass() -> None:
    variants = [_variant("OOR-A-01"), _variant("OOR-B-01"), _variant("OOR-C-01")]
    AdminProductService._assert_unique_skus_in_payload(variants)


def test_empty_payload_passes() -> None:
    AdminProductService._assert_unique_skus_in_payload([])


# ---------- Schema-level normalisation ----------


def test_tags_normalised_to_lowercase_unique() -> None:
    payload = AdminProductCreate(
        name="Test",
        base_price=Decimal("100"),
        tags=["Silk", "BRIDAL", "silk", "  KANCHIPURAM  ", ""],
    )
    assert payload.tags == ["silk", "bridal", "kanchipuram"]


def test_slug_pattern_enforced_when_provided() -> None:
    """Explicit slug must match `[a-z0-9](-[a-z0-9])*` pattern."""
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        AdminProductCreate(
            name="Test", base_price=Decimal("100"), slug="Has Space",
        )
    with pytest.raises(PydanticValidationError):
        AdminProductCreate(
            name="Test", base_price=Decimal("100"), slug="UPPER",
        )


def test_currency_uppercased() -> None:
    payload = AdminProductCreate(
        name="Test", base_price=Decimal("100"), currency="inr",
    )
    assert payload.currency == "INR"


def test_update_schema_rejects_status_field() -> None:
    """Admin must not change status via PATCH — uses lifecycle endpoints."""
    from app.schemas.admin_product import AdminProductUpdate

    # `extra="ignore"` silently drops status; the field doesn't exist
    # on the model so it's never applied. Confirm the dumped body is clean.
    body = AdminProductUpdate.model_validate({"status": "published", "name": "Valid Name"})
    dumped = body.model_dump(exclude_unset=True)
    assert "status" not in dumped
    assert dumped == {"name": "Valid Name"}


def test_update_schema_rejects_slug_field() -> None:
    from app.schemas.admin_product import AdminProductUpdate

    body = AdminProductUpdate.model_validate({"slug": "anything", "name": "Valid Name"})
    assert "slug" not in body.model_dump(exclude_unset=True)


def test_variant_initial_stock_bounded() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        AdminVariantCreate(sku="X", initial_stock=-1)
    with pytest.raises(PydanticValidationError):
        AdminVariantCreate(sku="X", initial_stock=200_000)
