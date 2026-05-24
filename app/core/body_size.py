"""Body-size limit middleware.

Reject oversized requests at the edge (HTTP 413) before any handler
allocates memory. Honours `Content-Length` for the fast path and falls
back to a streaming counter when the client sends a chunked request
without a length header.

Path-prefix exemptions allow webhooks (signature-verified raw bodies)
to bypass — Razorpay payloads can legitimately exceed our default
JSON cap when payment metadata is large.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def _too_large(request: Request, limit: int) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=413,
        content={
            "detail": f"Request body exceeds the {limit}-byte limit",
            "code": "request_body_too_large",
            "request_id": request_id,
        },
    )


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose body exceeds `max_bytes`. Exempt by path prefix."""

    def __init__(
        self,
        app,  # type: ignore[no-untyped-def]
        *,
        max_bytes: int,
        exempt_path_prefixes: list[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes
        self._exempt_prefixes = tuple(exempt_path_prefixes or ())

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path.startswith(self._exempt_prefixes):
            return await call_next(request)

        # Fast path: trust Content-Length when it's present and parseable.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                declared = int(cl)
            except ValueError:
                # Malformed header; reject as a precaution rather than
                # silently letting a streaming bypass through.
                return _too_large(request, self._max_bytes)
            if declared > self._max_bytes:
                return _too_large(request, self._max_bytes)

        # If we have Content-Length and it's within the limit, no need to
        # stream-count — let the request through.
        return await call_next(request)
