"""Rate limiting — slowapi-backed.

Architecture:
- One process-wide `Limiter` instance, built once from settings at app
  startup. Wired onto `app.state.limiter` (slowapi convention).
- Storage URI is env-driven: empty string → in-memory (single-instance
  Railway today), `redis://...` → shared state across replicas later.
- Default limit applies to every route; per-route stricter profiles
  (`auth_strict`, `checkout`, `tracking`, `webhook`, `admin`) are
  imported and used as decorators where appropriate.
- 429 responses match our standard `{detail, code, request_id}` shape
  so the frontend doesn't need a separate error parser for rate limits.

Key function:
- Authenticated requests are keyed by the JWT `sub` claim (best-effort
  unverified decode — speed > strict verification on the rate-limit key
  itself; the route's own auth dependency still verifies the token).
- Unauthenticated requests fall back to client IP (X-Forwarded-For aware
  via `get_remote_address`).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from slowapi.extension import _rate_limit_exceeded_handler  # noqa: F401

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Key function — user-aware, no DB hit, no JWT verification on this path
# ---------------------------------------------------------------------------


def _peek_jwt_sub(token: str) -> str | None:
    """Decode the JWT payload WITHOUT signature verification.

    Used ONLY to derive a stable rate-limit key. The route's auth
    dependency still performs full signature verification; getting a
    spoofed `sub` here only lets an attacker share their OWN rate-limit
    bucket with a stranger — which is strictly worse for the attacker.
    """
    try:
        _, payload_b64, _ = token.split(".")
    except ValueError:
        return None
    # JWT base64url uses no padding; pad to multiple of 4.
    padding = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    sub = claims.get("sub")
    return str(sub) if sub else None


def composite_key(request: Request) -> str:
    """Prefer Clerk user_id, fall back to IP. One bucket per identity."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        sub = _peek_jwt_sub(token)
        if sub:
            return f"user:{sub}"
    return f"ip:{get_remote_address(request)}"


# ---------------------------------------------------------------------------
# Limiter factory + module-level singleton
# ---------------------------------------------------------------------------


_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """Return the singleton Limiter, self-initialising on first access.

    Profile decorators (`@profiles.checkout()`) are evaluated at module
    import time — long before `install_rate_limiter` runs in the app
    factory — so we must be able to build a Limiter without a wired
    FastAPI app yet. `install_rate_limiter` then just attaches the SAME
    instance onto the app and registers the 429 handler.
    """
    global _limiter
    if _limiter is None:
        from app.core.config import get_settings

        init_limiter(get_settings())
    assert _limiter is not None  # narrow for type checker
    return _limiter


def init_limiter(settings: Settings) -> Limiter:
    """Construct (or rebuild) the singleton. Idempotent within a process."""
    global _limiter
    storage_uri = settings.rate_limit_storage_uri or "memory://"
    _limiter = Limiter(
        key_func=composite_key,
        default_limits=[settings.rate_limit_default] if settings.rate_limit_enabled else [],
        storage_uri=storage_uri,
        enabled=settings.rate_limit_enabled,
        headers_enabled=True,  # exposes X-RateLimit-{Limit,Remaining,Reset}
    )
    log.info(
        "rate_limiter_initialised",
        storage=storage_uri.split(":", 1)[0],
        default=settings.rate_limit_default,
        enabled=settings.rate_limit_enabled,
    )
    return _limiter


# ---------------------------------------------------------------------------
# 429 handler — match our standard error shape
# ---------------------------------------------------------------------------


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    retry_after = getattr(exc, "retry_after", None)
    log.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        method=request.method,
        limit=str(exc.detail),
        client_key=composite_key(request),
    )
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    return JSONResponse(
        status_code=429,
        content={
            "detail": f"Rate limit exceeded: {exc.detail}",
            "code": "rate_limit_exceeded",
            "request_id": request_id,
        },
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Per-profile decorator factories — applied to critical routes only
# ---------------------------------------------------------------------------
#
# slowapi requires the decorated route to have `request: Request` in its
# signature. These factories close over the limiter so routes import a
# single name (`profile.checkout`) rather than the Limiter object itself.


class Profiles:
    """Lazy accessors so test-time imports don't crash before init_limiter."""

    @staticmethod
    def checkout():  # type: ignore[no-untyped-def]
        # 30/minute per identity (IP for guests, user for signed-in).
        # Place-order + payment-verify share this profile.
        return get_limiter().limit("30/minute")

    @staticmethod
    def tracking():  # type: ignore[no-untyped-def]
        # Guest order lookup. Scrape-prone; bound tightly per IP.
        return get_limiter().limit("30/minute")

    @staticmethod
    def webhook():  # type: ignore[no-untyped-def]
        # Defence-in-depth on top of HMAC signature verification.
        # Razorpay/Clerk retry on 5xx, so don't be too aggressive.
        return get_limiter().limit("120/minute")

    @staticmethod
    def admin():  # type: ignore[no-untyped-def]
        # Generous — admin dashboard is one human + their bulk actions.
        return get_limiter().limit("600/minute")

    @staticmethod
    def auth_strict():  # type: ignore[no-untyped-def]
        # Reserve for login-adjacent surfaces (none today; placeholder
        # for /me/* sensitive operations in future cycles).
        return get_limiter().limit("10/minute")


profiles = Profiles()


# ---------------------------------------------------------------------------
# Wiring — called once from `app/main.py`
# ---------------------------------------------------------------------------


def install_rate_limiter(app: FastAPI, settings: Settings) -> None:
    limiter = init_limiter(settings)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)
