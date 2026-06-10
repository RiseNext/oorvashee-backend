"""CartService pure logic — no DB.

Availability is DERIVED: `_available(variant, reserved)` where `reserved` is the
SUM of active reservations (passed in by the caller, which queries it). The
deprecated stored inventory.reserved column is no longer consulted. The
reservation-aware add guard (`_guard_stock`) is now DB-backed and covered at the
API level in test_cart_availability.py.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import ValidationError
from app.models.enums import ProductStatus
from app.services.cart_service import CartService, _empty_cart


def _variant(*, stock: int, price: Decimal = Decimal("100")):
    """Build a fake variant graph good enough for the static helpers."""
    inv = SimpleNamespace(stock=stock)
    product = SimpleNamespace(
        id=uuid.uuid4(),
        slug="x",
        name="Sample",
        status=ProductStatus.PUBLISHED,
        base_price=price,
        images=[],
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        sku="SKU",
        is_active=True,
        color="Red",
        fabric="Silk",
        size=None,
        price_override=None,
        product=product,
        inventory=inv,
    )


def test_available_subtracts_derived_reserved() -> None:
    assert CartService._available(_variant(stock=10), 3) == 7


def test_available_floors_at_zero() -> None:
    assert CartService._available(_variant(stock=2), 5) == 0  # corruption guard


def test_available_no_inventory_row() -> None:
    v = _variant(stock=0)
    v.inventory = None
    assert CartService._available(v, 0) == 0


def test_guard_quantity_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        CartService._guard_quantity(0)


def test_guard_quantity_rejects_too_large() -> None:
    with pytest.raises(ValidationError):
        CartService._guard_quantity(100)


def test_empty_cart_shape() -> None:
    cart = _empty_cart()
    assert cart.items == []
    assert cart.item_count == 0
    assert cart.subtotal == Decimal("0")
    assert cart.id is None


def test_serialize_item_computes_line_total_and_max() -> None:
    variant = _variant(stock=10, price=Decimal("50.00"))
    item = MagicMock()
    item.id = uuid.uuid4()
    item.variant = variant
    item.quantity = 3

    read = CartService._serialize_item(item, 0)  # no active reservations
    assert read.unit_price == Decimal("50.00")
    assert read.line_total == Decimal("150.00")
    assert read.available is True
    assert read.max_quantity == 10


def test_serialize_item_unavailable_when_derived_reserved_understocks() -> None:
    variant = _variant(stock=10, price=Decimal("50.00"))
    item = MagicMock()
    item.id = uuid.uuid4()
    item.variant = variant
    item.quantity = 5  # 5 requested, but only 2 available after others' holds
    read = CartService._serialize_item(item, 8)  # derived reserved = 8
    assert read.max_quantity == 2
    assert read.available is False
