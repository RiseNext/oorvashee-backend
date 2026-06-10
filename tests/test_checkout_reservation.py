"""Phase 2 — checkout reservation flow + concurrency protection.

The headline test is `test_concurrent_checkout_one_winner`: two authenticated
users hit POST /checkout for the SAME 1-stock saree at the same moment. The
row-locked SELECT FOR UPDATE in CheckoutSessionService must let exactly ONE
win (200) and force the other to 409 (reservation_conflict) — never oversell.

These tests run against a real Postgres (the migrated `oorvashee_test` DB) so
the FOR UPDATE contention is genuine, not mocked. Auth is bypassed via a
dependency override that maps an `X-Test-Clerk-Id` header to a seeded user.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from decimal import Decimal

import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

# Deterministic, DB-backed, rate-limit-free environment BEFORE importing app.
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://oorvashee:oorvashee@localhost:5432/oorvashee_test",
)

CHECKOUT_URL = "/api/v1/checkout"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sm():
    """Live sessionmaker wired into db_state (so get_db works in-process)."""
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
    """In-process ASGI client with auth overridden to read X-Test-Clerk-Id."""
    from app.core.security import Principal, get_current_principal
    from app.main import create_app

    app = create_app()

    async def fake_principal(request: Request):
        clerk_id = request.headers.get("X-Test-Clerk-Id")
        assert clerk_id, "test request missing X-Test-Clerk-Id header"
        return Principal(
            clerk_user_id=clerk_id, email=None, role="customer", raw_claims={}
        )

    app.dependency_overrides[get_current_principal] = fake_principal

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _truncate(maker) -> None:
    async with maker() as s:
        await s.execute(text("TRUNCATE users, products RESTART IDENTITY CASCADE"))
        await s.commit()


async def _new_variant(s, *, stock: int, published: bool = True, active: bool = True):
    """Create product + variant + inventory(stock). Returns variant id."""
    from app.models.enums import ProductStatus
    from app.models.inventory import Inventory
    from app.models.product import Product
    from app.models.product_variant import ProductVariant

    tag = uuid.uuid4().hex[:8]
    product = Product(
        slug=f"saree-{tag}",
        name=f"Test Saree {tag}",
        base_price=Decimal("4999.00"),
        status=ProductStatus.PUBLISHED if published else ProductStatus.DRAFT,
    )
    s.add(product)
    await s.flush()
    variant = ProductVariant(
        product_id=product.id,
        sku=f"SKU-{tag}",
        color="Crimson",
        fabric="Silk",
        is_active=active,
        is_default=True,
    )
    s.add(variant)
    await s.flush()
    s.add(Inventory(variant_id=variant.id, stock=stock))
    await s.flush()
    return variant.id


async def _new_user_with_cart(s, *, lines: list[tuple[uuid.UUID, int]]):
    """Create a user + server cart containing the given (variant_id, qty) lines.
    Returns the user's clerk_user_id."""
    from app.models.cart import Cart, CartItem
    from app.models.user import User

    tag = uuid.uuid4().hex[:8]
    user = User(clerk_user_id=f"clerk-{tag}", email=f"u-{tag}@test.com")
    s.add(user)
    await s.flush()
    cart = Cart(user_id=user.id)
    s.add(cart)
    await s.flush()
    for variant_id, qty in lines:
        s.add(CartItem(cart_id=cart.id, variant_id=variant_id, quantity=qty))
    await s.flush()
    return user.clerk_user_id


async def _count_active_reservations(maker, variant_id: uuid.UUID) -> int:
    async with maker() as s:
        return int(
            await s.scalar(
                text(
                    "SELECT COUNT(*) FROM reservations "
                    "WHERE variant_id = :v AND status = 'reserved'"
                ),
                {"v": variant_id},
            )
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_concurrent_checkout_one_winner(sm, client):
    """THE test: two users, one unit, simultaneous checkout -> 1x200, 1x409."""
    await _truncate(sm)
    async with sm() as s:
        variant_id = await _new_variant(s, stock=1)
        clerk_a = await _new_user_with_cart(s, lines=[(variant_id, 1)])
        clerk_b = await _new_user_with_cart(s, lines=[(variant_id, 1)])
        await s.commit()

    async def checkout(clerk_id: str):
        return await client.post(
            CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk_id}
        )

    # Real concurrency — both in flight on the same inventory row.
    r1, r2 = await asyncio.gather(checkout(clerk_a), checkout(clerk_b))

    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], f"expected one 200 + one 409, got {codes}: {r1.text} || {r2.text}"

    winner = r1 if r1.status_code == 200 else r2
    loser = r2 if r1.status_code == 200 else r1
    assert loser.json()["code"] == "reservation_conflict"
    assert winner.json()["lines"][0]["reserved"] is True

    # Exactly one unit held — no oversell.
    assert await _count_active_reservations(sm, variant_id) == 1


async def test_multiline_all_or_nothing(sm, client):
    """If ANY line is unavailable, the WHOLE reservation rolls back."""
    await _truncate(sm)
    async with sm() as s:
        ok_variant = await _new_variant(s, stock=5)
        zero_variant = await _new_variant(s, stock=0)
        clerk = await _new_user_with_cart(
            s, lines=[(ok_variant, 1), (zero_variant, 1)]
        )
        await s.commit()

    resp = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk})
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "reservation_conflict"

    # No partial holds — nothing reserved for EITHER line.
    assert await _count_active_reservations(sm, ok_variant) == 0
    assert await _count_active_reservations(sm, zero_variant) == 0


async def test_mixed_quantities_reserve(sm, client):
    """A unique item (qty=1) and a bulk item (qty=20) both reserve correctly."""
    await _truncate(sm)
    async with sm() as s:
        unique_variant = await _new_variant(s, stock=1)
        bulk_variant = await _new_variant(s, stock=50)
        clerk = await _new_user_with_cart(
            s, lines=[(unique_variant, 1), (bulk_variant, 20)]
        )
        await s.commit()

    resp = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["lines"]) == 2
    assert all(line["reserved"] for line in body["lines"])
    assert await _count_active_reservations(sm, unique_variant) == 1
    assert await _count_active_reservations(sm, bulk_variant) == 1


async def test_idempotent_reload_refreshes_not_stacks(sm, client):
    """Reloading checkout reuses the session + refreshes the SAME reservation."""
    await _truncate(sm)
    async with sm() as s:
        variant_id = await _new_variant(s, stock=5)
        clerk = await _new_user_with_cart(s, lines=[(variant_id, 2)])
        await s.commit()

    r1 = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk})
    r2 = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk})
    assert r1.status_code == 200 and r2.status_code == 200, f"{r1.text} || {r2.text}"

    # Same session reused (one active session per user); ONE reservation row.
    assert r1.json()["session_id"] == r2.json()["session_id"]
    assert await _count_active_reservations(sm, variant_id) == 1
    # And it did not eat into availability twice (own hold excluded on reload).
    assert r2.json()["lines"][0]["reserved"] is True


async def test_empty_cart_rejected(sm, client):
    """No cart / empty cart -> 422 cart_empty, no session created."""
    await _truncate(sm)
    async with sm() as s:
        clerk = await _new_user_with_cart(s, lines=[])  # cart exists but empty
        await s.commit()

    resp = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk})
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "cart_empty", resp.text
