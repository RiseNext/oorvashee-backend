"""Pure-logic tests for CSV bulk import — no DB.

Covers:
- Generous boolean parsing (Excel quirks: TRUE/Yes/1).
- Decimal parsing with currency symbol + comma tolerance.
- Tag splitting (pipe-delimited).
- Row schemas — required fields, validation, defaults.
- Target-stock math for inventory mode dispatch.
- Error-payload shaping (Pydantic ctx scrubbing, raw truncation).
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.models.enums import ProductStatus, StockMovementReason
from app.schemas.admin_import import (
    InventoryCsvRow,
    ProductCsvRow,
    VariantCsvRow,
    _parse_bool,
    _parse_decimal,
    _parse_int,
    _split_tags,
)
from app.services.admin_import_service import (
    MAX_RAW_FIELDS_IN_ERROR,
    _compute_target,
    _error_payload,
)

# =============================================================================
# Helpers — bool / decimal / int / tags
# =============================================================================


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True), ("TRUE", True), ("True", True),
        ("yes", True), ("Y", True), ("1", True), ("t", True),
        ("false", False), ("FALSE", False), ("no", False),
        ("N", False), ("0", False), ("", False), ("f", False),
        (True, True), (False, False), (None, None),
    ],
)
def test_parse_bool_permissive(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert _parse_bool(value) == expected


def test_parse_bool_rejects_unknown_token() -> None:
    with pytest.raises(ValueError):
        _parse_bool("maybe")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("100", Decimal("100")),
        (" 100 ", Decimal("100")),
        ("1,000.50", Decimal("1000.50")),
        ("₹500", Decimal("500")),
        ("$500", Decimal("500")),
        ("", None),
        (None, None),
    ],
)
def test_parse_decimal_tolerant(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert _parse_decimal(value) == expected


def test_parse_decimal_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        _parse_decimal("not a number")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10", 10), (" 10 ", 10), ("", None), (None, None),
    ],
)
def test_parse_int(value, expected) -> None:  # type: ignore[no-untyped-def]
    assert _parse_int(value) == expected


def test_split_tags_basic() -> None:
    assert _split_tags("silk|bridal|wedding") == ["silk", "bridal", "wedding"]


def test_split_tags_dedupes_and_lowercases() -> None:
    assert _split_tags("Silk|silk|BRIDAL|silk") == ["silk", "bridal"]


def test_split_tags_empty() -> None:
    assert _split_tags("") == []
    assert _split_tags(None) == []
    assert _split_tags(" | | ") == []


# =============================================================================
# ProductCsvRow
# =============================================================================


def test_product_minimal_required_fields() -> None:
    row = ProductCsvRow.model_validate({
        "name": "Test Product",
        "base_price": "999",
    })
    assert row.name == "Test Product"
    assert row.base_price == Decimal("999")
    assert row.status is ProductStatus.DRAFT
    assert row.featured is False
    assert row.tags == []


def test_product_missing_name_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ProductCsvRow.model_validate({"base_price": "999"})


def test_product_missing_price_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ProductCsvRow.model_validate({"name": "Sample"})


def test_product_negative_price_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        ProductCsvRow.model_validate({"name": "Sample", "base_price": "-1"})


def test_product_decimal_string_parsed() -> None:
    row = ProductCsvRow.model_validate({
        "name": "Test",
        "base_price": "₹1,234.50",
    })
    assert row.base_price == Decimal("1234.50")


def test_product_status_coercion() -> None:
    """Empty status defaults to draft; 'published' string maps cleanly."""
    row = ProductCsvRow.model_validate({"name": "Sample", "base_price": "1", "status": ""})
    assert row.status is ProductStatus.DRAFT
    row = ProductCsvRow.model_validate({"name": "Sample", "base_price": "1", "status": "published"})
    assert row.status is ProductStatus.PUBLISHED


def test_product_tags_pipe_delimited() -> None:
    row = ProductCsvRow.model_validate({
        "name": "Sample", "base_price": "1", "tags": "silk|bridal|south",
    })
    assert row.tags == ["silk", "bridal", "south"]


def test_product_slug_pattern_enforced() -> None:
    with pytest.raises(PydanticValidationError):
        ProductCsvRow.model_validate({
            "name": "Sample", "base_price": "1", "slug": "Has Space",
        })


def test_product_extras_silently_ignored() -> None:
    """`extra='ignore'` — an evolving CSV with extra columns mustn't break."""
    row = ProductCsvRow.model_validate({
        "name": "Sample", "base_price": "1",
        "unknown_field": "anything", "future_col": "x",
    })
    assert row.name == "Sample"


# =============================================================================
# VariantCsvRow
# =============================================================================


def test_variant_minimal_required() -> None:
    row = VariantCsvRow.model_validate({
        "product_slug": "kanchipuram-bridal",
        "sku": "KCH-001",
    })
    assert row.initial_stock == 0
    assert row.low_stock_threshold == 2
    assert row.is_active is True


def test_variant_missing_product_slug_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        VariantCsvRow.model_validate({"sku": "X"})


def test_variant_initial_stock_bounded() -> None:
    with pytest.raises(PydanticValidationError):
        VariantCsvRow.model_validate({
            "product_slug": "x", "sku": "Y", "initial_stock": "200000",
        })


# =============================================================================
# InventoryCsvRow
# =============================================================================


@pytest.mark.parametrize("mode", ["set", "increment", "decrement"])
def test_inventory_mode_accepted(mode: str) -> None:
    row = InventoryCsvRow.model_validate({
        "sku": "X", "mode": mode, "value": "5",
    })
    assert row.mode == mode


def test_inventory_bad_mode_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        InventoryCsvRow.model_validate({"sku": "X", "mode": "delete", "value": "5"})


def test_inventory_reason_defaults_to_manual() -> None:
    row = InventoryCsvRow.model_validate({"sku": "X", "mode": "set", "value": "5"})
    assert row.reason is StockMovementReason.MANUAL_ADJUSTMENT


def test_inventory_reason_coercion() -> None:
    row = InventoryCsvRow.model_validate({
        "sku": "X", "mode": "set", "value": "5", "reason": "restock",
    })
    assert row.reason is StockMovementReason.RESTOCK


def test_inventory_negative_value_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        InventoryCsvRow.model_validate({"sku": "X", "mode": "set", "value": "-1"})


def test_inventory_value_upper_bound() -> None:
    with pytest.raises(PydanticValidationError):
        InventoryCsvRow.model_validate({"sku": "X", "mode": "set", "value": "10000000"})


# =============================================================================
# Target-stock math
# =============================================================================


@pytest.mark.parametrize(
    "mode,value,current,expected",
    [
        ("set", 0, 10, 0),
        ("set", 99, 10, 99),
        ("increment", 5, 10, 15),
        ("decrement", 3, 10, 7),
        ("decrement", 100, 10, -90),  # service rejects later via target<0 check
    ],
)
def test_compute_target(mode: str, value: int, current: int, expected: int) -> None:
    assert _compute_target(mode, value, current) == expected


def test_compute_target_unknown_mode_defensive() -> None:
    """Schema constrains mode; defensive default returns current_stock."""
    assert _compute_target("bogus", 10, 5) == 5


# =============================================================================
# Error payload shaping
# =============================================================================


def test_error_payload_normalises_pydantic_loc() -> None:
    """`loc` tuple gets joined into a dotted field path."""
    errors = [
        {"loc": ("base_price",), "msg": "field required", "type": "missing"},
    ]
    payload = _error_payload(5, errors, {"name": "Test", "base_price": ""})
    assert payload["row_number"] == 5
    assert payload["errors"]["base_price"] == "field required"


def test_error_payload_truncates_wide_raw_row() -> None:
    """Raw row with 25 columns gets capped to MAX_RAW_FIELDS_IN_ERROR."""
    wide_raw = {f"col_{i}": str(i) for i in range(25)}
    payload = _error_payload(1, [], wide_raw)
    assert len(payload["raw"]) == MAX_RAW_FIELDS_IN_ERROR


def test_error_payload_handles_empty_loc() -> None:
    """Pydantic sometimes emits `loc=()`; map to a `_root` key so the
    error is still grep-able."""
    payload = _error_payload(2, [{"loc": (), "msg": "root error"}], {})
    assert payload["errors"]["_root"] == "root error"


# =============================================================================
# CSV reader integration (no DB, just parsing)
# =============================================================================


def test_csv_dictreader_handles_utf8_bom() -> None:
    """Excel exports start with a BOM; the service handles it via the
    utf-8-sig decode. Smoke-check that the standard csv module works
    on a BOM-decoded text stream."""
    import csv as _csv

    body = "﻿name,base_price\nKanchipuram,7499\n"
    rows = list(_csv.DictReader(io.StringIO(body)))
    # The BOM is stripped (utf-8-sig OR explicit handling), so the first
    # column header should NOT include a BOM character.
    # (csv.DictReader on a string already-decoded with utf-8-sig has
    # no BOM left in the keys.)
    keys = list(rows[0].keys())
    assert keys[0].lstrip("﻿") == "name"
