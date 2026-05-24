"""Security-headers middleware.

Headers we send on every response:
- `Strict-Transport-Security` — force HTTPS for the next year + subdomains.
  Railway terminates TLS; this is for browsers, not for us.
- `X-Content-Type-Options: nosniff` — prevent MIME sniffing attacks.
- `X-Frame-Options: DENY` — disallow framing entirely (we have no embeds).
- `Referrer-Policy: strict-origin-when-cross-origin` — leak only origin
  on cross-origin navigations, never path or query.
- `Cross-Origin-Opener-Policy: same-origin` — isolate browsing context.
- `X-Permitted-Cross-Domain-Policies: none` — block Flash/PDF cross-domain.
- `Server: oorvashee` — replace Uvicorn's banner so version recon is harder.

Explicitly NOT set here:
- `Content-Security-Policy` — owned by the Next.js frontend, not the API.
  Sending CSP on a JSON-only API surface adds no value and confuses devs.
- `Permissions-Policy` — same rationale.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Server": "oorvashee",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append security headers to every response (success + error alike)."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for name, value in _HEADERS.items():
            # `setdefault` semantics: if a downstream component (e.g. the
            # CORS middleware) already set the header, respect their choice.
            if name not in response.headers:
                response.headers[name] = value
        return response
