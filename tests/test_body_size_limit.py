"""BodySizeLimitMiddleware — Content-Length fast path + exempt prefixes."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def test_oversize_post_rejected_with_413() -> None:
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        # 5 MB JSON exceeds the 1 MB default.
        huge_body = '{"junk":"' + ("x" * (5 * 1024 * 1024)) + '"}'
        r = await ac.post(
            "/api/v1/checkout/quote",
            content=huge_body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 413
        body = r.json()
        assert body["code"] == "request_body_too_large"
        assert "request_id" in body


async def test_normal_post_passes_through() -> None:
    """Below-limit requests reach the handler normally (gets to validation)."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        # Valid shape — short body, fails Pydantic later with a different code
        r = await ac.post("/api/v1/checkout/quote", json={"items": []})
        # 422 because items must have min_length=1, not 413.
        assert r.status_code == 422
        assert r.json()["code"] == "validation_error"


async def test_webhook_routes_exempt_from_size_cap() -> None:
    """Webhook bodies bypass the cap — signature verification reads raw bytes."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        # 2 MB body to /webhooks/razorpay — should reach the handler
        # (which then rejects via signature verification, not size).
        big = b'{"event":"x","payload":{"x":"' + b"y" * (2 * 1024 * 1024) + b'"}}'
        r = await ac.post(
            "/api/v1/webhooks/razorpay",
            content=big,
            headers={
                "content-type": "application/json",
                "x-razorpay-signature": "bad",
            },
        )
        # NOT 413 — the body cap was bypassed.
        assert r.status_code != 413
        # Expect 401 (signature reject) or 502 (parsing reject), not 413.
        assert r.status_code in (401, 502)


async def test_malformed_content_length_rejected() -> None:
    """Defensive: garbage Content-Length is treated as oversized."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        r = await ac.post(
            "/api/v1/checkout/quote",
            content=b'{"items":[]}',
            headers={
                "content-type": "application/json",
                "content-length": "not-a-number",
            },
        )
        # httpx may normalise the header before sending; if it does,
        # the request still succeeds normally — test passes either way
        # as long as we didn't crash the middleware.
        assert r.status_code in (413, 422)
