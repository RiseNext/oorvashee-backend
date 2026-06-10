"""Phase 5 — admin dashboard: derived reserved / sold / available + reservation
lists + reservation-aware adjust guard. Real Postgres (`oorvashee_test`)."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://oorvashee:oorvashee@localhost:5432/oorvashee_test",
)

ADMIN = "/api/v1/admin/inventory"


@pytest_asyncio.fixture
async def sm():
    from app.core.config import get_settings
    from app.db.session import (
        build_engine,
        build_sessionmaker,
        dispose_engine,
        set_db_state,
    )

    get_settings.cache_clear()
    settings = get_settings()
    engine = build_engine(settings)
    maker = build_sessionmaker(engine)
    set_db_state(engine, maker)
    yield maker
    await dispose_engine()


@pytest_asyncio.fixture
async def client(sm):
    from app.core.security import Principal, get_current_principal
    from app.main import create_app

    app = create_app()

    async def fake_admin():
        return Principal(
            clerk_user_id="admin-clerk", email="admin@test.com",
            role="admin", raw_claims={},
        )

    app.dependency_overrides[get_current_principal] = fake_admin
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _reset(maker) -> None:
    async with maker() as s:
        await s.execute(text("TRUNCATE users, products RESTART IDENTITY CASCADE"))
        await s.commit()


async def _make_variant(s, *, stock: int, sold: int = 0):
    from app.models.enums import ProductStatus
    from app.models.inventory import Inventory
    from app.models.product import Product
    from app.models.product_variant import ProductVariant

    tag = uuid.uuid4().hex[:8]
    p = Product(slug=f"s-{tag}", name=f"Saree {tag}", base_price=Decimal("4999.00"), status=ProductStatus.PUBLISHED)
    s.add(p)
    await s.flush()
    v = ProductVariant(product_id=p.id, sku=f"SKU-{tag}", color="Red", fabric="Silk", is_active=True, is_default=True)
    s.add(v)
    await s.flush()
    s.add(Inventory(variant_id=v.id, stock=stock, sold_quantity=sold))
    await s.flush()
    return v.id


async def _add_reservation(s, *, variant_id, session_status, reservation_status, qty, expired=False):
    from app.db.base import utcnow
    from app.models.checkout_session import CheckoutSession
    from app.models.reservation import Reservation
    from app.models.user import User

    tag = uuid.uuid4().hex[:8]
    u = User(clerk_user_id=f"c-{tag}", email=f"{tag}@t.com")
    s.add(u)
    await s.flush()
    exp = utcnow() - timedelta(minutes=1) if expired else utcnow() + timedelta(minutes=5)
    cs = CheckoutSession(user_id=u.id, status=session_status, expires_at=exp)
    s.add(cs)
    await s.flush()
    r = Reservation(
        checkout_session_id=cs.id, variant_id=variant_id, user_id=u.id,
        quantity=qty, status=reservation_status, expires_at=exp,
    )
    s.add(r)
    await s.flush()
    return r.id


def _find(items, variant_id):
    return next(i for i in items if i["variant_id"] == str(variant_id))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_list_and_detail_show_derived_reserved_and_sold(sm, client):
    from app.models.enums import CheckoutSessionStatus as CS
    from app.models.enums import ReservationStatus as RS

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=10, sold=3)
        await _add_reservation(s, variant_id=variant_id, session_status=CS.ACTIVE, reservation_status=RS.RESERVED, qty=2)
        await _add_reservation(s, variant_id=variant_id, session_status=CS.PAYMENT_PROCESSING, reservation_status=RS.PAYMENT_PROCESSING, qty=1)
        await _add_reservation(s, variant_id=variant_id, session_status=CS.COMPLETED, reservation_status=RS.COMPLETED, qty=5)
        await _add_reservation(s, variant_id=variant_id, session_status=CS.CANCELLED, reservation_status=RS.CANCELLED, qty=4)
        await s.commit()

    # List: derived reserved = 2 (RESERVED) + 1 (PP) = 3; available = 7; sold = 3.
    lr = await client.get(ADMIN)
    assert lr.status_code == 200, lr.text
    item = _find(lr.json()["items"], variant_id)
    assert item["stock"] == 10
    assert item["reserved"] == 3
    assert item["available"] == 7
    assert item["sold"] == 3

    # Detail: same numbers + reservation_counts (ROW counts per bucket).
    dr = await client.get(f"{ADMIN}/{variant_id}")
    assert dr.status_code == 200, dr.text
    d = dr.json()
    assert (d["reserved"], d["available"], d["sold"]) == (3, 7, 3)
    assert d["reservation_counts"] == {"active": 2, "expired": 0, "completed": 1, "cancelled": 1}


async def test_health_uses_derived_reserved_and_sold(sm, client):
    from app.models.enums import CheckoutSessionStatus as CS
    from app.models.enums import ReservationStatus as RS

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=10, sold=4)
        await _add_reservation(s, variant_id=v, session_status=CS.ACTIVE, reservation_status=RS.RESERVED, qty=3)
        # completed/cancelled don't count toward reserved
        await _add_reservation(s, variant_id=v, session_status=CS.COMPLETED, reservation_status=RS.COMPLETED, qty=2)
        await s.commit()

    hr = await client.get(f"{ADMIN}/health")
    assert hr.status_code == 200, hr.text
    h = hr.json()
    assert h["total_stock_units"] == 10
    assert h["total_reserved_units"] == 3
    assert h["available_units"] == 7
    assert h["total_sold_units"] == 4


async def test_reservations_list_filters_by_status(sm, client):
    from app.models.enums import CheckoutSessionStatus as CS
    from app.models.enums import ReservationStatus as RS

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=10)
        await _add_reservation(s, variant_id=v, session_status=CS.ACTIVE, reservation_status=RS.RESERVED, qty=2, expired=True)
        await _add_reservation(s, variant_id=v, session_status=CS.PAYMENT_PROCESSING, reservation_status=RS.PAYMENT_PROCESSING, qty=1)
        await _add_reservation(s, variant_id=v, session_status=CS.COMPLETED, reservation_status=RS.COMPLETED, qty=5)
        await s.commit()

    # Active tab = reserved + payment_processing.
    ar = await client.get(f"{ADMIN}/reservations", params=[("variant_id", str(v)), ("status", "reserved"), ("status", "payment_processing")])
    assert ar.status_code == 200, ar.text
    active = ar.json()
    assert active["total"] == 2
    assert all(i["bucket"] == "active" for i in active["items"])
    # The expired-but-unswept RESERVED row is flagged.
    flagged = [i for i in active["items"] if i["status"] == "reserved"]
    assert flagged and flagged[0]["is_expired"] is True

    # Completed tab.
    cr = await client.get(f"{ADMIN}/reservations", params={"variant_id": str(v), "status": "completed"})
    assert cr.json()["total"] == 1
    assert cr.json()["items"][0]["bucket"] == "completed"


async def test_adjust_blocked_below_active_reservations(sm, client):
    from app.models.enums import CheckoutSessionStatus as CS
    from app.models.enums import ReservationStatus as RS

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=5)
        await _add_reservation(s, variant_id=v, session_status=CS.ACTIVE, reservation_status=RS.RESERVED, qty=3)
        await s.commit()

    # Setting stock to 2 (< 3 reserved) must be rejected.
    bad = await client.post(
        f"{ADMIN}/{v}/adjust",
        json={"mode": "set", "value": 2, "reason": "manual_adjustment"},
    )
    assert bad.status_code == 409, bad.text
    assert bad.json()["code"] == "adjustment_below_reserved"

    # Setting stock to exactly 3 (== reserved) is allowed.
    ok = await client.post(
        f"{ADMIN}/{v}/adjust",
        json={"mode": "set", "value": 3, "reason": "manual_adjustment"},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["stock_after"] == 3
    assert body["reserved"] == 3
