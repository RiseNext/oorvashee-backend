"""Phase 3 — payment flow + Razorpay webhook + expiry guards (e2e).

Real Postgres (`oorvashee_test`). Razorpay's create_order is mocked (no
network); the webhook is exercised for real with a valid HMAC signature.

Covers the Phase 3 scope:
  - POST /checkout/{id}/pay -> PAYMENT_PROCESSING + Razorpay handoff
  - payment.captured webhook -> COMPLETED, stock-=qty, sold_quantity+=qty
  - payment.failed webhook   -> CANCELLED, no stock change
  - duplicate webhook delivery is idempotent (stock moves once)
  - 15-min PAYMENT_PROCESSING backstop (timeout recovery)
  - 3-min RESERVED sweep frees stock back (timeout recovery)
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from decimal import Decimal
from hashlib import sha256

import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

WEBHOOK_SECRET = "test_wh_secret_phase3"
KEY_SECRET = "test_key_secret_phase3"

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
os.environ.setdefault("RAZORPAY_KEY_SECRET", KEY_SECRET)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://oorvashee:oorvashee@localhost:5432/oorvashee_test",
)

CHECKOUT_URL = "/api/v1/checkout"
WEBHOOK_URL = "/api/v1/webhooks/razorpay"

PAY_BODY = {
    "customer": {"email": "buyer@test.com", "phone": "9990001111", "full_name": "Buyer"},
    "shipping_address": {
        "recipient_name": "Buyer",
        "phone": "9990001111",
        "line1": "1 Marine Drive",
        "city": "Mumbai",
        "state": "Maharashtra",
        "postal_code": "400001",
        "country": "IN",
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
async def client(sm, monkeypatch):
    from app.core.security import Principal, get_current_principal
    from app.integrations import razorpay_client as rc
    from app.integrations.razorpay_client import RazorpayOrder, inr_to_paise
    from app.main import create_app

    # Mock Razorpay order creation — no network.
    counter = {"n": 0}

    async def fake_create_order(self, *, amount, currency, receipt, notes=None):
        counter["n"] += 1
        return RazorpayOrder(
            id=f"order_test_{counter['n']}_{uuid.uuid4().hex[:8]}",
            amount_paise=inr_to_paise(amount),
            currency=currency,
        )

    monkeypatch.setattr(rc.RazorpayClient, "create_order", fake_create_order)

    app = create_app()

    async def fake_principal(request: Request):
        return Principal(
            clerk_user_id=request.headers["X-Test-Clerk-Id"],
            email=None,
            role="customer",
            raw_claims={},
        )

    app.dependency_overrides[get_current_principal] = fake_principal

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


async def _make_variant(s, *, stock: int, reserved: int = 0):
    from app.models.enums import ProductStatus
    from app.models.inventory import Inventory
    from app.models.product import Product
    from app.models.product_variant import ProductVariant

    tag = uuid.uuid4().hex[:8]
    p = Product(
        slug=f"s-{tag}",
        name=f"Saree {tag}",
        base_price=Decimal("4999.00"),
        status=ProductStatus.PUBLISHED,
    )
    s.add(p)
    await s.flush()
    v = ProductVariant(
        product_id=p.id, sku=f"SKU-{tag}", color="Red", fabric="Silk",
        is_active=True, is_default=True,
    )
    s.add(v)
    await s.flush()
    s.add(Inventory(variant_id=v.id, stock=stock, reserved=reserved))
    await s.flush()
    return v.id


async def _make_user_cart(s, *, variant_id, qty: int):
    from app.models.cart import Cart, CartItem
    from app.models.user import User

    tag = uuid.uuid4().hex[:8]
    u = User(clerk_user_id=f"c-{tag}", email=f"{tag}@t.com")
    s.add(u)
    await s.flush()
    cart = Cart(user_id=u.id)
    s.add(cart)
    await s.flush()
    s.add(CartItem(cart_id=cart.id, variant_id=variant_id, quantity=qty))
    await s.flush()
    return u.clerk_user_id


async def _reserve_and_pay(client, clerk_id):
    """POST /checkout then /pay. Returns (order_number, razorpay_order_id)."""
    r = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk_id})
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]
    p = await client.post(
        f"{CHECKOUT_URL}/{session_id}/pay",
        headers={"X-Test-Clerk-Id": clerk_id},
        json=PAY_BODY,
    )
    assert p.status_code == 201, p.text
    body = p.json()
    return session_id, body["order_number"], body["payment"]["razorpay_order_id"]


def _captured_event(razorpay_order_id, *, event_id):
    return {
        "event": "payment.captured",
        "id": event_id,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{uuid.uuid4().hex[:12]}",
                    "order_id": razorpay_order_id,
                    "amount": 499900,
                    "status": "captured",
                }
            }
        },
    }


def _failed_event(razorpay_order_id, *, event_id):
    return {
        "event": "payment.failed",
        "id": event_id,
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{uuid.uuid4().hex[:12]}",
                    "order_id": razorpay_order_id,
                    "error_description": "card declined",
                    "status": "failed",
                }
            }
        },
    }


async def _post_webhook(client, event: dict):
    raw = json.dumps(event).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), raw, sha256).hexdigest()
    return await client.post(
        WEBHOOK_URL,
        content=raw,
        headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
    )


async def _inv(maker, variant_id):
    async with maker() as s:
        row = (
            await s.execute(
                text(
                    "SELECT stock, sold_quantity FROM inventory WHERE variant_id=:v"
                ),
                {"v": variant_id},
            )
        ).one()
    return {"stock": row[0], "sold": row[1]}


async def _statuses(maker, *, order_number, variant_id):
    async with maker() as s:
        o = (
            await s.execute(
                text(
                    "SELECT status, payment_status FROM orders WHERE order_number=:o"
                ),
                {"o": order_number},
            )
        ).one()
        res = (
            await s.execute(
                text("SELECT status FROM reservations WHERE variant_id=:v"),
                {"v": variant_id},
            )
        ).one()
        sess = (
            await s.execute(
                text(
                    "SELECT cs.status FROM checkout_sessions cs "
                    "JOIN reservations r ON r.checkout_session_id=cs.id "
                    "WHERE r.variant_id=:v"
                ),
                {"v": variant_id},
            )
        ).one()
    return {
        "order_status": o[0],
        "payment_status": o[1],
        "reservation": res[0],
        "session": sess[0],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_successful_payment_captures_and_decrements(sm, client):
    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=3)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, rzp_order_id = await _reserve_and_pay(client, clerk)

    # After pay, BEFORE capture: stock untouched, everything PAYMENT_PROCESSING.
    assert (await _inv(sm, variant_id)) == {"stock": 3, "sold": 0}
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "pending"
    assert st["reservation"] == "payment_processing"
    assert st["session"] == "payment_processing"

    resp = await _post_webhook(client, _captured_event(rzp_order_id, event_id="evt_cap_1"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "captured"

    # After capture: stock-=2, sold+=2, everything COMPLETED + PAID.
    assert (await _inv(sm, variant_id)) == {"stock": 1, "sold": 2}
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "paid"
    assert st["reservation"] == "completed"
    assert st["session"] == "completed"


async def test_failed_payment_cancels_without_stock_change(sm, client):
    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=3)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, rzp_order_id = await _reserve_and_pay(client, clerk)

    resp = await _post_webhook(client, _failed_event(rzp_order_id, event_id="evt_fail_1"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "failed_recorded"

    # No stock movement; reservation + session CANCELLED; order FAILED.
    assert (await _inv(sm, variant_id)) == {"stock": 3, "sold": 0}
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "failed"
    assert st["reservation"] == "cancelled"
    assert st["session"] == "cancelled"


async def test_duplicate_webhook_is_idempotent(sm, client):
    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=5)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, rzp_order_id = await _reserve_and_pay(client, clerk)

    ev = _captured_event(rzp_order_id, event_id="evt_dup")
    r1 = await _post_webhook(client, ev)
    r2 = await _post_webhook(client, ev)  # identical redelivery
    assert r1.json()["status"] == "captured"
    assert r2.json()["status"] == "duplicate"

    # Stock decremented EXACTLY once.
    assert (await _inv(sm, variant_id)) == {"stock": 3, "sold": 2}


async def test_payment_processing_backstop_timeout(sm, client):
    """15-min backstop: a stuck PAYMENT_PROCESSING is cancelled, no stock change.
    And the 3-min RESERVED sweep must NOT touch PAYMENT_PROCESSING."""
    from app.services.reservation_lifecycle import ReservationLifecycleService

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=3)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, _ = await _reserve_and_pay(client, clerk)

    # The RESERVED sweep must leave PAYMENT_PROCESSING alone, even if "expired".
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE reservations SET expires_at = now() - interval '1 min' "
                "WHERE status='payment_processing'"
            )
        )
        await s.execute(
            text(
                "UPDATE checkout_sessions SET expires_at = now() - interval '1 min' "
                "WHERE status='payment_processing'"
            )
        )
        await s.commit()
    async with sm() as s:
        reserved_sweep = await ReservationLifecycleService(s).sweep_expired_reservations()
        await s.commit()
    assert reserved_sweep["reservations"] == 0  # PP untouched by the 3-min rule
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["reservation"] == "payment_processing"

    # Now the 15-min backstop reclaims it.
    async with sm() as s:
        counts = await ReservationLifecycleService(s).sweep_payment_processing_backstop()
        await s.commit()
    assert counts["reservations"] == 1 and counts["sessions"] == 1

    assert (await _inv(sm, variant_id)) == {"stock": 3, "sold": 0}  # no stock change
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["reservation"] == "cancelled"
    assert st["session"] == "cancelled"
    assert st["payment_status"] == "failed"


async def test_verify_is_informational_only_webhook_is_truth(sm, client):
    """For reservation orders, /payments/verify must NOT mutate inventory or
    complete the order — only the webhook does (webhook = source of truth)."""
    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=3)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, rzp_order_id = await _reserve_and_pay(client, clerk)

    # Frontend redirect verify with a VALID Razorpay payment signature.
    pay_id = f"pay_{uuid.uuid4().hex[:12]}"
    msg = f"{rzp_order_id}|{pay_id}".encode()
    sig = hmac.new(KEY_SECRET.encode(), msg, sha256).hexdigest()
    vr = await client.post(
        "/api/v1/payments/verify",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json={
            "order_number": order_number,
            "razorpay_order_id": rzp_order_id,
            "razorpay_payment_id": pay_id,
            "razorpay_signature": sig,
        },
    )
    assert vr.status_code == 200, vr.text

    # Verify did NOT decrement stock or complete the reservation.
    assert (await _inv(sm, variant_id)) == {"stock": 3, "sold": 0}
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "pending"
    assert st["reservation"] == "payment_processing"

    # The WEBHOOK performs the one and only inventory mutation.
    resp = await _post_webhook(
        client, _captured_event(rzp_order_id, event_id="evt_after_verify")
    )
    assert resp.json()["status"] == "captured"
    assert (await _inv(sm, variant_id)) == {"stock": 1, "sold": 2}
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "paid"
    assert st["reservation"] == "completed"


async def test_reserved_expiry_frees_stock(sm, client):
    """3-min timeout recovery: an abandoned RESERVED hold expires and the unit
    becomes available to another customer again."""
    from app.services.reservation_lifecycle import ReservationLifecycleService

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=1)
        clerk_a = await _make_user_cart(s, variant_id=variant_id, qty=1)
        clerk_b = await _make_user_cart(s, variant_id=variant_id, qty=1)
        await s.commit()

    # A reserves the only unit.
    ra = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk_a})
    assert ra.status_code == 200
    # B cannot — it's held.
    rb = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk_b})
    assert rb.status_code == 409

    # A abandons; their hold "ages out".
    async with sm() as s:
        await s.execute(
            text(
                "UPDATE reservations SET expires_at = now() - interval '1 min' "
                "WHERE status='reserved'"
            )
        )
        await s.execute(
            text(
                "UPDATE checkout_sessions SET expires_at = now() - interval '1 min' "
                "WHERE status='active'"
            )
        )
        await s.commit()
    async with sm() as s:
        swept = await ReservationLifecycleService(s).sweep_expired_reservations()
        await s.commit()
    assert swept["reservations"] == 1 and swept["sessions"] == 1

    # B can now reserve — stock freed automatically (no stock mutation occurred).
    rb2 = await client.post(CHECKOUT_URL, headers={"X-Test-Clerk-Id": clerk_b})
    assert rb2.status_code == 200, rb2.text
    assert (await _inv(sm, variant_id))["stock"] == 1


# ---------------------------------------------------------------------------
# Phase 7 defect fixes (D1-D5)
# ---------------------------------------------------------------------------


async def test_d1_legacy_checkout_orders_is_disabled(sm, client):
    """D1: the legacy POST /checkout/orders flow is OFF by default (410),
    so the stored-reserved ledger can't coexist with derived reservations."""
    body = {
        "items": [{"variant_id": str(uuid.uuid4()), "quantity": 1}],
        "customer": {"email": "x@t.com", "phone": "9990001111", "full_name": "X"},
        "shipping_address": {
            "recipient_name": "X", "phone": "9990001111", "line1": "1 St",
            "city": "Mumbai", "state": "MH", "postal_code": "400001", "country": "IN",
        },
        "payment_method": "razorpay",
    }
    r = await client.post(
        "/api/v1/checkout/orders",
        headers={"Idempotency-Key": uuid.uuid4().hex},
        json=body,
    )
    assert r.status_code == 410, r.text


async def test_d2_commit_sale_clamps_legacy_reserved(sm, client):
    """D2: a capture on a variant carrying a non-zero LEGACY stored reserved
    must not violate the reserved<=stock CHECK (which would 500 the webhook)."""
    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=3, reserved=2)  # legacy hold
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, rzp = await _reserve_and_pay(client, clerk)
    resp = await _post_webhook(client, _captured_event(rzp, event_id="evt_d2"))
    assert resp.status_code == 200 and resp.json()["status"] == "captured", resp.text

    # stock 3->1, sold 0->2, and stored reserved clamped 2->1 (<= stock).
    assert (await _inv(sm, variant_id)) == {"stock": 1, "sold": 2}
    async with sm() as s:
        reserved = (
            await s.execute(
                text("SELECT reserved FROM inventory WHERE variant_id=:v"),
                {"v": variant_id},
            )
        ).scalar_one()
    assert reserved == 1
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "paid"


async def test_d3_late_capture_after_backstop_needs_refund(sm, client):
    """D3: a capture arriving after the unit was resold must NOT 500/loop —
    it returns 200, flags needs_refund, and never drives stock negative."""
    from app.services.reservation_lifecycle import ReservationLifecycleService

    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=1)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=1)
        await s.commit()

    _, order_number, rzp = await _reserve_and_pay(client, clerk)

    # Backstop fires (gateway never called back), then the unit is resold.
    async with sm() as s:
        await s.execute(text(
            "UPDATE reservations SET expires_at = now() - interval '1 min' WHERE status='payment_processing'"
        ))
        await s.execute(text(
            "UPDATE checkout_sessions SET expires_at = now() - interval '1 min' WHERE status='payment_processing'"
        ))
        await s.commit()
    async with sm() as s:
        await ReservationLifecycleService(s).sweep_payment_processing_backstop()
        await s.commit()
    async with sm() as s:  # simulate the freed unit being resold
        await s.execute(text("UPDATE inventory SET stock = 0 WHERE variant_id=:v"), {"v": variant_id})
        await s.commit()

    # Late capture arrives.
    r1 = await _post_webhook(client, _captured_event(rzp, event_id="evt_late"))
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "captured_needs_refund"

    # No oversell (stock stays 0, not -1); order NOT paid; payment flagged.
    assert (await _inv(sm, variant_id))["stock"] == 0
    async with sm() as s:
        order_ps, pay_status, pay_reason = (
            await s.execute(
                text(
                    "SELECT o.payment_status, p.status, p.failure_reason "
                    "FROM orders o JOIN payments p ON p.order_id=o.id "
                    "WHERE o.order_number=:o"
                ),
                {"o": order_number},
            )
        ).one()
    assert order_ps == "failed"          # backstop already failed it; not flipped to paid
    assert pay_status == "captured"      # money WAS captured
    assert pay_reason == "captured_out_of_stock_needs_refund"

    # Razorpay retries the SAME event → deduped, still no oversell.
    r2 = await _post_webhook(client, _captured_event(rzp, event_id="evt_late"))
    assert r2.json()["status"] == "duplicate"
    assert (await _inv(sm, variant_id))["stock"] == 0


async def test_d5_failed_after_capture_is_ignored(sm, client):
    """D5: a payment.failed (new event id) arriving after a successful capture
    must NOT regress a PAID order or release a fulfilled reservation."""
    await _reset(sm)
    async with sm() as s:
        variant_id = await _make_variant(s, stock=3)
        clerk = await _make_user_cart(s, variant_id=variant_id, qty=2)
        await s.commit()

    _, order_number, rzp = await _reserve_and_pay(client, clerk)
    cap = await _post_webhook(client, _captured_event(rzp, event_id="evt_cap"))
    assert cap.json()["status"] == "captured"

    # A late, distinct payment.failed event for the same order.
    fail = await _post_webhook(client, _failed_event(rzp, event_id="evt_fail_late"))
    assert fail.status_code == 200, fail.text

    # Order stays PAID, stock stays decremented, reservation stays COMPLETED.
    assert (await _inv(sm, variant_id)) == {"stock": 1, "sold": 2}
    st = await _statuses(sm, order_number=order_number, variant_id=variant_id)
    assert st["payment_status"] == "paid"
    assert st["reservation"] == "completed"


async def test_d4_cancel_restocks_only_when_stock_was_decremented(sm, client):
    """D4: admin cancel must restock a PAID order but NOT a never-captured
    (PENDING) order — restocking the latter inflates inventory."""
    from app.schemas.admin_order import CancelOrderRequest
    from app.services.admin_order_service import AdminOrderService

    async def _cancel(order_number):
        async with sm() as s:
            oid = (
                await s.execute(
                    text("SELECT id FROM orders WHERE order_number=:o"),
                    {"o": order_number},
                )
            ).scalar_one()
            actor = (
                await s.execute(text("SELECT id FROM users LIMIT 1"))
            ).scalar_one()
            await AdminOrderService(s).cancel_order(
                oid, CancelOrderRequest(reason="phase7 test"), actor_user_id=actor,
            )
            await s.commit()

    # --- PENDING (never captured) → NO restock ---
    await _reset(sm)
    async with sm() as s:
        v1 = await _make_variant(s, stock=3)
        clerk1 = await _make_user_cart(s, variant_id=v1, qty=2)
        await s.commit()
    _, pending_order, _ = await _reserve_and_pay(client, clerk1)  # no webhook
    assert (await _inv(sm, v1))["stock"] == 3  # not decremented yet
    await _cancel(pending_order)
    assert (await _inv(sm, v1))["stock"] == 3  # D4: NOT inflated to 5

    # --- PAID (captured) → restock on cancel ---
    await _reset(sm)
    async with sm() as s:
        v2 = await _make_variant(s, stock=3)
        clerk2 = await _make_user_cart(s, variant_id=v2, qty=2)
        await s.commit()
    _, paid_order, rzp2 = await _reserve_and_pay(client, clerk2)
    await _post_webhook(client, _captured_event(rzp2, event_id="evt_d4"))
    assert (await _inv(sm, v2))["stock"] == 1  # decremented on capture
    await _cancel(paid_order)
    assert (await _inv(sm, v2))["stock"] == 3  # restored


# ---------------------------------------------------------------------------
# Production incident fixes: release-on-cancel + My Orders empty until paid
# ---------------------------------------------------------------------------


async def test_cancel_releases_hold_and_cancels_unpaid_order(sm, client):
    """Razorpay dismissed/failed → POST /checkout/{id}/cancel frees the hold
    immediately and cancels the unpaid order (no phantom 'placed' order)."""
    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=1)
        clerk = await _make_user_cart(s, variant_id=v, qty=1)
        await s.commit()

    session_id, order_number, _ = await _reserve_and_pay(client, clerk)
    assert (await _inv(sm, v))["stock"] == 1  # not decremented (held only)

    c = await client.post(
        f"{CHECKOUT_URL}/{session_id}/cancel", headers={"X-Test-Clerk-Id": clerk}
    )
    assert c.status_code == 204, c.text

    st = await _statuses(sm, order_number=order_number, variant_id=v)
    assert st["reservation"] == "cancelled"   # hold released → others see available
    assert st["session"] == "cancelled"
    assert st["order_status"] == "cancelled"  # unpaid order cancelled
    assert st["payment_status"] == "failed"
    assert (await _inv(sm, v))["stock"] == 1  # no stock mutation


async def test_capture_after_cancel_flags_needs_refund_not_revived(sm, client):
    """If a capture lands AFTER the user cancelled, don't revive the order —
    flag needs_refund, no stock decrement."""
    await _reset(sm)
    async with sm() as s:
        v = await _make_variant(s, stock=1)
        clerk = await _make_user_cart(s, variant_id=v, qty=1)
        await s.commit()

    session_id, order_number, rzp = await _reserve_and_pay(client, clerk)
    await client.post(
        f"{CHECKOUT_URL}/{session_id}/cancel", headers={"X-Test-Clerk-Id": clerk}
    )

    r = await _post_webhook(client, _captured_event(rzp, event_id="evt_after_cancel"))
    assert r.status_code == 200
    assert r.json()["status"] == "captured_needs_refund"

    assert (await _inv(sm, v))["stock"] == 1  # not decremented
    st = await _statuses(sm, order_number=order_number, variant_id=v)
    assert st["order_status"] == "cancelled"  # not revived to placed/paid


async def test_my_orders_excludes_unpaid(sm):
    """My Orders stays empty until payment: PENDING/FAILED hidden, PAID shown."""
    from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus
    from app.models.order import Order
    from app.models.user import User
    from app.repositories.order_repo import OrderRepository

    await _reset(sm)
    async with sm() as s:
        u = User(clerk_user_id="c-mo", email="mo@t.com")
        s.add(u)
        await s.flush()

        def _order(pstatus):
            return Order(
                order_number=uuid.uuid4().hex[:12].upper(),
                user_id=u.id,
                customer_name="X",
                email="x@t.com",
                phone="9990001111",
                payment_method=PaymentMethod.RAZORPAY,
                subtotal=Decimal("100.00"),
                total=Decimal("100.00"),
                shipping_address={"line1": "x"},
                status=OrderStatus.PLACED,
                payment_status=pstatus,
            )

        s.add(_order(PaymentStatus.PAID))
        s.add(_order(PaymentStatus.PENDING))
        s.add(_order(PaymentStatus.FAILED))
        await s.commit()
        uid = u.id

    async with sm() as s:
        rows, total = await OrderRepository(s).list_for_user(uid, limit=50, offset=0)
    assert total == 1
    assert rows[0].payment_status.value == "paid"
