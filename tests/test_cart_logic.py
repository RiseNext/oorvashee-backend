"""CartService pure logic — no DB."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import OutOfStockError, ValidationError
from app.models.enums import ProductStatus
from app.services.cart_service import CartService, _empty_cart


def _variant(*, stock: int, reserved: int = 0, price: Decimal = Decimal("100")):
    """Build a fake variant graph good enough for the static helpers."""
    inv = SimpleNamespace(stock=stock, reserved=reserved)
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
        price_override=None,
        product=product,
        inventory=inv,
    )


def test_available_stock_subtracts_reserved() -> None:
    v = _variant(stock=10, reserved=3)
    assert CartService._available_stock(v) == 7


def test_available_stock_floors_at_zero() -> None:
    v = _variant(stock=2, reserved=5)  # data corruption guard
    assert CartService._available_stock(v) == 0


def test_available_stock_no_inventory_row() -> None:
    v = _variant(stock=0)
    v.inventory = None
    assert CartService._available_stock(v) == 0


def test_guard_quantity_rejects_zero() -> None:
    with pytest.raises(ValidationError):
        CartService._guard_quantity(0)


def test_guard_quantity_rejects_too_large() -> None:
    with pytest.raises(ValidationError):
        CartService._guard_quantity(100)


def test_guard_stock_passes_when_within() -> None:
    CartService._guard_stock(_variant(stock=10), requested_qty=5)


def test_guard_stock_raises_out_of_stock() -> None:
    with pytest.raises(OutOfStockError) as exc_info:
        CartService._guard_stock(_variant(stock=3, reserved=2), requested_qty=5)
    assert "1" in exc_info.value.message  # available=1


def test_empty_cart_shape() -> None:
    cart = _empty_cart()
    assert cart.items == []
    assert cart.item_count == 0
    assert cart.subtotal == Decimal("0")
    assert cart.id is None


def test_serialize_item_computes_line_total() -> None:
    variant = _variant(stock=10, price=Decimal("50.00"))
    item = MagicMock()
    item.id = uuid.uuid4()
    item.variant = variant
    item.quantity = 3

    read = CartService._serialize_item(item)
    assert read.unit_price == Decimal("50.00")
    assert read.line_total == Decimal("150.00")
    assert read.available is True
    assert read.max_quantity == 10


def test_serialize_item_marks_unavailable_when_understocked() -> None:
    variant = _variant(stock=2, price=Decimal("50.00"))
    item = MagicMock()
    item.id = uuid.uuid4()
    item.variant = variant
    item.quantity = 5  # over available
    read = CartService._serialize_item(item)
    assert read.available is False
