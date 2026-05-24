"""Security headers middleware must apply to every response."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import create_app

_EXPECTED = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Server": "oorvashee",
}


async def test_security_headers_on_success() -> None:
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        r = await ac.get("/api/v1/health/live")
        assert r.status_code == 200
        for name, value in _EXPECTED.items():
            assert r.headers.get(name) == value, f"missing {name}"


async def test_security_headers_on_404() -> None:
    """4xx error responses must still be hardened."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        r = await ac.get("/api/v1/nonexistent")
        assert r.status_code == 404
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"


async def test_request_id_echoed_back() -> None:
    """RequestContextMiddleware echoes X-Request-ID on the response."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        r = await ac.get("/api/v1/health/live")
        assert "X-Request-ID" in r.headers
        assert len(r.headers["X-Request-ID"]) > 0


async def test_request_id_honours_inbound_header() -> None:
    """A fronting CDN can stamp the id once; we propagate it through."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        r = await ac.get(
            "/api/v1/health/live",
            headers={"X-Request-ID": "my-correlation-abc123"},
        )
        assert r.headers["X-Request-ID"] == "my-correlation-abc123"


async def test_request_id_ignores_oversized_inbound() -> None:
    """Don't let a malicious client smuggle a 10 KB log key."""
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as ac:
        huge = "x" * 200
        r = await ac.get("/api/v1/health/live", headers={"X-Request-ID": huge})
        assert r.headers["X-Request-ID"] != huge
        assert len(r.headers["X-Request-ID"]) <= 64
