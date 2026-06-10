"""Phase 6 — DB-derived product availability states on the public PDP."""

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


@pytest_asyncio.fixture
async def sm():
    from app.core.config import get_settings
    from app.db.session import build_engine, build_sessionmaker, dispose_engine, set_db_state

    get_settings.cache_clear()
    settings = get_settings()
    engine = build_engine(settings)
    maker = build_sessionmaker(engine)
    set_db_state(engine, maker)
    yield maker
    await dispose_engine()


@pytest_asyncio.fixture
async def client(sm):
    from app.main import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _reset(maker) -> None:
    async with maker() as s:
        await s.execute(text("TRUNCATE users, products RESTART IDENTITY CASCADE"))
        await s.commit()


async def _make_product(s, *, stock: int):
    from app.models.enums import ProductStatus
    from app.models.inventory import Inventory
    from app.models.product import Product
    from app.models.product_variant import ProductVariant

    tag = uuid.uuid4().hex[:8]
    p = Product(slug=f"saree-{tag}", name=f"Saree {tag}", base_price=Decimal("4999.00"), status=ProductStatus.PUBLISHED)
    s.add(p)
    await s.flush()
    v = ProductVariant(product_id=p.id, sku=f"SKU-{tag}", color="Red", is_active=True, is_default=True)
    s.add(v)
    await s.flush()
    s.add(Inventory(variant_id=v.id, stock=stock))
    await s.flush()
    return p.slug, v.id


async def _hold(s, *, variant_id, qty: int):
    from app.db.base import utcnow
    from app.models.checkout_session import CheckoutSession
    from app.models.enums import CheckoutSessionStatus, ReservationStatus
    from app.models.reservation import Reservation
    from app.models.user import User

    tag = uuid.uuid4().hex[:8]
    u = User(clerk_user_id=f"c-{tag}", email=f"{tag}@t.com")
    s.add(u)
    await s.flush()
    exp = utcnow() + timedelta(minutes=5)
    cs = CheckoutSession(user_id=u.id, status=CheckoutSessionStatus.ACTIVE, expires_at=exp)
    s.add(cs)
    await s.flush()
    s.add(Reservation(checkout_session_id=cs.id, variant_id=variant_id, user_id=u.id, quantity=qty, status=ReservationStatus.RESERVED, expires_at=exp))
    await s.flush()


async def test_availability_states_from_db(sm, client):
    # (stock, active_reserved) -> (expected_state, expected_available_quantity)
    cases = [
        (20, 0, "in_stock", 20),
        (8, 0, "low", 8),
        (4, 0, "selling_fast", 4),
        (1, 0, "last_one", 1),
        (3, 3, "reserved", 0),   # physical stock exists but all held
        (0, 0, "out_of_stock", 0),
    ]
    for stock, held, state, avail in cases:
        await _reset(sm)
        async with sm() as s:
            slug, variant_id = await _make_product(s, stock=stock)
            if held:
                await _hold(s, variant_id=variant_id, qty=held)
            await s.commit()

        r = await client.get(f"/api/v1/products/{slug}")
        assert r.status_code == 200, r.text
        v = r.json()["variants"][0]
        assert v["availability_state"] == state, (stock, held, v)
        assert v["available_quantity"] == avail, (stock, held, v)
        assert v["available"] is (avail > 0)


async def test_expired_hold_does_not_reduce_availability(sm, client):
    """A hold past its expiry must NOT count (lazy expiry) — unit shows available."""
    from app.db.base import utcnow
    from app.models.checkout_session import CheckoutSession
    from app.models.enums import CheckoutSessionStatus, ReservationStatus
    from app.models.reservation import Reservation
    from app.models.user import User

    await _reset(sm)
    async with sm() as s:
        slug, variant_id = await _make_product(s, stock=1)
        tag = uuid.uuid4().hex[:8]
        u = User(clerk_user_id=f"c-{tag}", email=f"{tag}@t.com")
        s.add(u)
        await s.flush()
        past = utcnow() - timedelta(minutes=1)
        cs = CheckoutSession(user_id=u.id, status=CheckoutSessionStatus.ACTIVE, expires_at=past)
        s.add(cs)
        await s.flush()
        s.add(Reservation(checkout_session_id=cs.id, variant_id=variant_id, user_id=u.id, quantity=1, status=ReservationStatus.RESERVED, expires_at=past))
        await s.commit()

    r = await client.get(f"/api/v1/products/{slug}")
    v = r.json()["variants"][0]
    assert v["availability_state"] == "last_one"
    assert v["available_quantity"] == 1
