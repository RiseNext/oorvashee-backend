"""Cart availability is DERIVED from active reservations (Model A).

Covers the quantity-clamp bug fix at the backend boundary: a cart line's
max_quantity + available reflect SUM(active reservations by OTHERS), so the
frontend stepper/checkout can constrain correctly. Backend stays source of truth.
"""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

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


async def _reset(maker) -> None:
    async with maker() as s:
        await s.execute(text("TRUNCATE users, products RESTART IDENTITY CASCADE"))
        await s.commit()


async def _make_variant(s, *, stock: int):
    from app.models.enums import ProductStatus
    from app.models.inventory import Inventory
    from app.models.product import Product
    from app.models.product_variant import ProductVariant

    tag = uuid.uuid4().hex[:8]
    p = Product(slug=f"s-{tag}", name=f"Saree {tag}", base_price=Decimal("4999.00"), status=ProductStatus.PUBLISHED)
    s.add(p)
    await s.flush()
    v = ProductVariant(product_id=p.id, sku=f"SKU-{tag}", color="Red", is_active=True, is_default=True)
    s.add(v)
    await s.flush()
    s.add(Inventory(variant_id=v.id, stock=stock))
    await s.flush()
    return v.id


async def _make_user(s):
    from app.models.user import User

    tag = uuid.uuid4().hex[:8]
    u = User(clerk_user_id=f"c-{tag}", email=f"{tag}@t.com")
    s.add(u)
    await s.flush()
    return u.id


async def _make_cart_item(s, *, user_id, variant_id, qty):
    from app.models.cart import Cart, CartItem

    cart = Cart(user_id=user_id)
    s.add(cart)
    await s.flush()
    s.add(CartItem(cart_id=cart.id, variant_id=variant_id, quantity=qty))
    await s.flush()


async def _hold(s, *, user_id, variant_id, qty):
    from app.db.base import utcnow
    from app.models.checkout_session import CheckoutSession
    from app.models.enums import CheckoutSessionStatus, ReservationStatus
    from app.models.reservation import Reservation

    exp = utcnow() + timedelta(minutes=5)
    cs = CheckoutSession(user_id=user_id, status=CheckoutSessionStatus.ACTIVE, expires_at=exp)
    s.add(cs)
    await s.flush()
    s.add(Reservation(
        checkout_session_id=cs.id, variant_id=variant_id, user_id=user_id,
        quantity=qty, status=ReservationStatus.RESERVED, expires_at=exp,
    ))
    await s.flush()
    return cs.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_cart_line_reflects_others_active_reservations(sm):
    """Inventory 'changes' while the item sits in the cart: another customer
    reserves units → this line's max_quantity drops + it's flagged unavailable."""
    from app.services.cart_service import CartService

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=5)
        u = await _make_user(s)
        await _make_cart_item(s, user_id=u, variant_id=v, qty=3)
        other = await _make_user(s)
        await _hold(s, user_id=other, variant_id=v, qty=4)  # available = 5 - 4 = 1
        await s.commit()

    async with sm() as s:
        cart = await CartService(s).get_cart(u)
    line = cart.items[0]
    assert line.max_quantity == 1          # derived, not stock(5)
    assert line.available is False         # qty 3 > available 1
    assert cart.has_unavailable_items is True


async def test_cart_line_fully_available_when_no_holds(sm):
    from app.services.cart_service import CartService

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=5)
        u = await _make_user(s)
        await _make_cart_item(s, user_id=u, variant_id=v, qty=3)
        await s.commit()

    async with sm() as s:
        cart = await CartService(s).get_cart(u)
    line = cart.items[0]
    assert line.max_quantity == 5
    assert line.available is True
    assert cart.has_unavailable_items is False


async def test_add_item_guard_uses_derived_available(sm):
    from app.core.exceptions import OutOfStockError
    from app.services.cart_service import CartService

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=5)
        u = await _make_user(s)
        other = await _make_user(s)
        await _hold(s, user_id=other, variant_id=v, qty=4)  # available = 1
        await s.commit()

    # Adding 2 exceeds derived availability (1) → rejected.
    async with sm() as s:
        with pytest.raises(OutOfStockError):
            await CartService(s).add_item(u, v, 2)
    # Adding 1 is fine.
    async with sm() as s:
        cart = await CartService(s).add_item(u, v, 1)
        await s.commit()
    assert cart.items[0].quantity == 1


async def test_cart_excludes_users_own_active_reservation(sm):
    """The user's OWN live checkout hold must not count against their cart."""
    from app.services.cart_service import CartService

    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=5)
        u = await _make_user(s)
        await _make_cart_item(s, user_id=u, variant_id=v, qty=3)
        await _hold(s, user_id=u, variant_id=v, qty=2)  # the user's OWN hold
        await s.commit()

    async with sm() as s:
        cart = await CartService(s).get_cart(u)
    line = cart.items[0]
    assert line.max_quantity == 5          # own hold excluded → full stock
    assert line.available is True
