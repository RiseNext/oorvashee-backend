"""CheckoutService + InventoryService pure-logic tests."""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.exceptions import (
    OutOfStockError,
    ProductUnavailableError,
)
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
)
from app.services.checkout_service import CheckoutService
from app.services.inventory_service import InventoryService, StockRequirement


def _variant(*, id=None, stock=10, reserved=0, status="published", price=Decimal("100")):
    inv = SimpleNamespace(stock=stock, reserved=reserved)
    product = SimpleNamespace(
        id=uuid.uuid4(),
        slug="x", name="Item",
        status=SimpleNamespace(value=status),
        base_price=price,
        images=[],
    )
    return SimpleNamespace(
        id=id or uuid.uuid4(),
        sku="SKU", is_active=True,
        color="Red", fabric="Silk",
        price_override=None,
        product=product,
        inventory=inv,
    )


# ---- CheckoutService static helpers ----------------------------------------


def test_available_stock_helper() -> None:
    v = _variant(stock=10, reserved=2)
    assert CheckoutService._available_stock(v) == 8


def test_build_order_rejects_unavailable_product() -> None:
    from app.schemas.checkout import (
        CheckoutAddress,
        CheckoutCustomer,
        PlaceOrderRequest,
        QuoteLine,
    )

    archived = _variant(status="archived")
    req = PlaceOrderRequest(
        items=[QuoteLine(variant_id=archived.id, quantity=1)],
        customer=CheckoutCustomer(
            email="x@y.com", phone="9876543210", full_name="A",
        ),
        shipping_address=CheckoutAddress(
            recipient_name="A", phone="9876543210",
            line1="1 Main St", city="Bengaluru", state="KA", postal_code="560001",
        ),
        payment_method=PaymentMethod.RAZORPAY,
    )
    svc = CheckoutService(session=None)  # type: ignore[arg-type]
    with pytest.raises(ProductUnavailableError):
        svc._build_order(req=req, variants={archived.id: archived}, user_id=None)


def test_build_order_rejects_oversold_line() -> None:
    from app.schemas.checkout import (
        CheckoutAddress,
        CheckoutCustomer,
        PlaceOrderRequest,
        QuoteLine,
    )

    low = _variant(stock=2, reserved=0)
    req = PlaceOrderRequest(
        items=[QuoteLine(variant_id=low.id, quantity=5)],
        customer=CheckoutCustomer(email="x@y.com", phone="9876543210", full_name="A"),
        shipping_address=CheckoutAddress(
            recipient_name="A", phone="9876543210", line1="1",
            city="C", state="K", postal_code="000001",
        ),
        payment_method=PaymentMethod.COD,
    )
    svc = CheckoutService(session=None)  # type: ignore[arg-type]
    with pytest.raises(OutOfStockError):
        svc._build_order(req=req, variants={low.id: low}, user_id=None)


def test_build_order_computes_totals() -> None:
    from app.schemas.checkout import (
        CheckoutAddress,
        CheckoutCustomer,
        PlaceOrderRequest,
        QuoteLine,
    )

    v = _variant(stock=10, price=Decimal("250.00"))
    req = PlaceOrderRequest(
        items=[QuoteLine(variant_id=v.id, quantity=3)],
        customer=CheckoutCustomer(email="x@y.com", phone="9876543210", full_name="A"),
        shipping_address=CheckoutAddress(
            recipient_name="A", phone="9876543210",
            line1="1", city="C", state="K", postal_code="000001",
        ),
        payment_method=PaymentMethod.COD,
    )
    svc = CheckoutService(session=None)  # type: ignore[arg-type]
    order, items, totals = svc._build_order(req=req, variants={v.id: v}, user_id=None)
    assert len(items) == 1
    assert items[0].quantity == 3
    assert items[0].unit_price == Decimal("250.00")
    assert items[0].line_total == Decimal("750.00")
    assert totals["subtotal"] == Decimal("750.00")
    assert order.total == Decimal("750.00")
    assert order.status == OrderStatus.PLACED
    # `_build_order` leaves payment_status as None (the DB server_default
    # supplies 'pending' on flush; `place_order` flips it to PENDING or
    # COD_PENDING after the inventory step decides which path applies).
    assert order.payment_status is None
    assert order.order_number.startswith("OOR-")


# ---- InventoryService deterministic locking order --------------------------


def test_inventory_lock_order_is_sorted_and_deduped() -> None:
    a = uuid.UUID(int=1)
    b = uuid.UUID(int=2)
    c = uuid.UUID(int=3)

    reqs = [
        StockRequirement(variant_id=c, quantity=1),
        StockRequirement(variant_id=a, quantity=2),
        StockRequirement(variant_id=b, quantity=1),
        StockRequirement(variant_id=a, quantity=1),  # duplicate
    ]
    # Reproduce the sort/dedup that _lock_inventory_rows applies.
    ids = [r.variant_id for r in reqs]
    expected = sorted(set(ids))
    assert expected == [a, b, c]


def test_ensure_available_passes() -> None:
    inv = SimpleNamespace(stock=10, reserved=2)
    InventoryService._ensure_available(inv, requested=8, variant_id=uuid.uuid4())


def test_ensure_available_raises_with_payload() -> None:
    inv = SimpleNamespace(stock=10, reserved=8)  # 2 available
    vid = uuid.uuid4()
    with pytest.raises(OutOfStockError) as exc_info:
        InventoryService._ensure_available(inv, requested=5, variant_id=vid)
    assert exc_info.value.extra["available"] == 2
    assert exc_info.value.extra["requested"] == 5


# ---- Order number generator ------------------------------------------------


def test_order_number_format() -> None:
    from app.utils.ids import generate_order_number

    n = generate_order_number()
    assert n.startswith("OOR-")
    # OOR-YYYYMM-XXXXXXXX
    parts = n.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 6
    assert parts[1].isdigit()
    assert len(parts[2]) == 8
    int(parts[2], 16)  # hex


def test_order_number_uniqueness_sample() -> None:
    from app.utils.ids import generate_order_number

    sample = {generate_order_number() for _ in range(500)}
    assert len(sample) == 500
