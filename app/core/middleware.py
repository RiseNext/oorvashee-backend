"""Request-scoped middleware: request ID + structured access log + Sentry tag."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger
from app.core.sentry import set_request_tag

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def _latency_band(duration_ms: float) -> str:
    """Coarse band for structured-log dashboards.

    Cheaper than recomputing percentiles in the log pipeline and gives
    on-call a one-glance "are we slow right now" signal.
    """
    if duration_ms < 50:
        return "fast"
    if duration_ms < 250:
        return "ok"
    if duration_ms < 1000:
        return "slow"
    return "very_slow"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request_id, binds it everywhere downstream cares about it.

    Per-request invariants:
    - `request.state.request_id` — readable inside handlers / deps.
    - structlog contextvars — every log line carries `request_id`, `method`,
      `path` automatically.
    - Sentry scope tag — every Sentry event in this request carries the
      same `request_id` for cross-system correlation.
    - Response header `X-Request-ID` — the client can echo it back when
      reporting issues; on-call lookup is one grep away.

    Honours an incoming `X-Request-ID` (≤64 chars) so a fronting CDN or
    Next.js middleware can stamp the id once and have it propagate.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming_id = request.headers.get(REQUEST_ID_HEADER)
        request_id = (
            incoming_id if incoming_id and len(incoming_id) <= 64 else uuid.uuid4().hex
        )
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        set_request_tag(request_id)

        start = time.perf_counter()
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            if response is not None:
                response.headers[REQUEST_ID_HEADER] = request_id
            log.info(
                "http_request",
                status=status_code,
                duration_ms=duration_ms,
                latency_band=_latency_band(duration_ms),
                client=request.client.host if request.client else None,
            )
            structlog.contextvars.clear_contextvars()
