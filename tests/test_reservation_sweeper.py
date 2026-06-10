"""Phase 4 — in-process reservation expiry sweeper.

Real Postgres (`oorvashee_test`). Verifies the sweep tick, the advisory-lock
single-runner guard, and the loop start/stop. The underlying transitions are
also covered in test_checkout_payment.py; here we focus on the cron mechanics.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import text

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://oorvashee:oorvashee@localhost:5432/oorvashee_test",
)

from app.tasks.reservation_sweeper import (  # noqa: E402
    RESERVATION_SWEEP_LOCK_KEY,
    ReservationSweeper,
    run_sweep_once,
)


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    v = ProductVariant(product_id=p.id, sku=f"SKU-{tag}", is_active=True, is_default=True)
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


async def _make_session(
    s, *, user_id, variant_id, session_status, reservation_status, expired, with_order=False
):
    from app.db.base import utcnow
    from app.models.checkout_session import CheckoutSession
    from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus
    from app.models.order import Order
    from app.models.reservation import Reservation
    from app.utils.ids import generate_order_number

    exp = utcnow() - timedelta(minutes=1) if expired else utcnow() + timedelta(minutes=5)
    cs = CheckoutSession(user_id=user_id, status=session_status, expires_at=exp)
    s.add(cs)
    await s.flush()
    r = Reservation(
        checkout_session_id=cs.id, variant_id=variant_id, user_id=user_id,
        quantity=1, status=reservation_status, expires_at=exp,
    )
    s.add(r)
    await s.flush()
    order_number = None
    if with_order:
        order = Order(
            order_number=generate_order_number(), user_id=user_id, checkout_session_id=cs.id,
            customer_name="X", email="x@t.com", phone="9990001111",
            payment_method=PaymentMethod.RAZORPAY, subtotal=Decimal("4999"), total=Decimal("4999"),
            shipping_address={"city": "Mumbai"}, status=OrderStatus.PLACED,
            payment_status=PaymentStatus.PENDING,
        )
        s.add(order)
        await s.flush()
        order_number = order.order_number
    return cs.id, r.id, order_number


async def _res_status(maker, reservation_id):
    async with maker() as s:
        return (
            await s.execute(
                text("SELECT status FROM reservations WHERE id=:i"), {"i": reservation_id}
            )
        ).scalar_one()


async def _sess_status(maker, session_id):
    async with maker() as s:
        return (
            await s.execute(
                text("SELECT status FROM checkout_sessions WHERE id=:i"), {"i": session_id}
            )
        ).scalar_one()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_sweep_once_expires_and_backstops(sm):
    from app.models.enums import CheckoutSessionStatus, ReservationStatus

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=5)
        u1, u2, u3 = await _make_user(s), await _make_user(s), await _make_user(s)
        # expired RESERVED -> should EXPIRE
        exp_res = await _make_session(
            s, user_id=u1, variant_id=variant_id,
            session_status=CheckoutSessionStatus.ACTIVE,
            reservation_status=ReservationStatus.RESERVED, expired=True,
        )
        # expired PAYMENT_PROCESSING (+ order) -> should CANCEL + order FAILED
        exp_pp = await _make_session(
            s, user_id=u2, variant_id=variant_id,
            session_status=CheckoutSessionStatus.PAYMENT_PROCESSING,
            reservation_status=ReservationStatus.PAYMENT_PROCESSING,
            expired=True, with_order=True,
        )
        # NON-expired RESERVED -> control, must be left alone
        live = await _make_session(
            s, user_id=u3, variant_id=variant_id,
            session_status=CheckoutSessionStatus.ACTIVE,
            reservation_status=ReservationStatus.RESERVED, expired=False,
        )
        await s.commit()

    counts = await run_sweep_once(sm)
    assert counts is not None
    assert counts["expired"]["reservations"] == 1
    assert counts["expired"]["sessions"] == 1
    assert counts["backstop"]["reservations"] == 1
    assert counts["backstop"]["sessions"] == 1

    assert await _res_status(sm, exp_res[1]) == "expired"
    assert await _sess_status(sm, exp_res[0]) == "expired"
    assert await _res_status(sm, exp_pp[1]) == "cancelled"
    assert await _sess_status(sm, exp_pp[0]) == "cancelled"
    assert await _res_status(sm, live[1]) == "reserved"  # control untouched
    assert await _sess_status(sm, live[0]) == "active"

    async with sm() as s:
        order_ps = (
            await s.execute(
                text("SELECT payment_status FROM orders WHERE order_number=:o"),
                {"o": exp_pp[2]},
            )
        ).scalar_one()
        inv = (
            await s.execute(
                text("SELECT stock, sold_quantity FROM inventory WHERE variant_id=:v"),
                {"v": variant_id},
            )
        ).one()
    assert order_ps == "failed"
    assert tuple(inv) == (5, 0)  # expiry/backstop NEVER touch stock


async def test_advisory_lock_makes_single_runner(sm):
    """While one transaction holds the advisory lock, a sweep tick is skipped."""
    from app.models.enums import CheckoutSessionStatus, ReservationStatus

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=2)
        u = await _make_user(s)
        ids = await _make_session(
            s, user_id=u, variant_id=variant_id,
            session_status=CheckoutSessionStatus.ACTIVE,
            reservation_status=ReservationStatus.RESERVED, expired=True,
        )
        await s.commit()

    holder = sm()
    try:
        got = (
            await holder.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"),
                {"k": RESERVATION_SWEEP_LOCK_KEY},
            )
        ).scalar_one()
        assert got is True
        # Lock is held by `holder`'s open transaction -> the sweep can't run.
        skipped = await run_sweep_once(sm)
        assert skipped is None
        assert await _res_status(sm, ids[1]) == "reserved"  # not swept
    finally:
        await holder.rollback()  # releases the xact lock
        await holder.close()

    # Lock free now -> the sweep runs.
    ran = await run_sweep_once(sm)
    assert ran is not None and ran["expired"]["reservations"] == 1
    assert await _res_status(sm, ids[1]) == "expired"


async def test_sweeper_loop_runs_then_stops(sm):
    from app.models.enums import CheckoutSessionStatus, ReservationStatus

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=2)
        u = await _make_user(s)
        ids = await _make_session(
            s, user_id=u, variant_id=variant_id,
            session_status=CheckoutSessionStatus.ACTIVE,
            reservation_status=ReservationStatus.RESERVED, expired=True,
        )
        await s.commit()

    sweeper = ReservationSweeper(sm, interval=0.05)
    sweeper.start()
    # Give the loop a few ticks to pick up the expired row.
    for _ in range(40):
        if await _res_status(sm, ids[1]) == "expired":
            break
        await asyncio.sleep(0.05)
    await sweeper.stop()

    assert await _res_status(sm, ids[1]) == "expired"
    # After stop(), the task is cleared and no longer sweeping.
    assert sweeper._task is None
