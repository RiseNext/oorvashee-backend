"""Domain exceptions + global FastAPI exception handlers.

All non-HTTP errors raised inside services/repositories should be one of these
domain types. The handler shapes them into the uniform error response.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------- Domain exception hierarchy ----------


class AppError(Exception):
    """Base class for all domain errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(AppError):
    status_code = 422  # HTTP_422 — Starlette renamed the constant in 0.40+
    code = "validation_error"


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


class PaymentFailedError(AppError):
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    code = "payment_failed"


class IntegrationError(AppError):
    """Upstream provider (Razorpay, Cloudinary, Resend, Clerk) failed."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "integration_error"


class OutOfStockError(AppError):
    """Requested quantity exceeds available stock at the moment of the check."""

    status_code = status.HTTP_409_CONFLICT
    code = "out_of_stock"


class ProductUnavailableError(AppError):
    """Product is archived / draft / unavailable and cannot be transacted."""

    status_code = status.HTTP_409_CONFLICT
    code = "product_unavailable"


class ReservationConflictError(AppError):
    """A cart-level checkout reservation could not be created atomically.

    Raised when one or more lines are no longer available at the moment of the
    row-locked reservation — stock held by other active reservations, sold, or
    the product/variant became unavailable. The whole reservation rolls back
    (no partial holds); `extra["lines"]` carries the per-line breakdown so the
    frontend can show exactly which item failed. Returns HTTP 409.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "reservation_conflict"


class IdempotencyConflictError(AppError):
    """Same Idempotency-Key seen previously on a different route/user/body."""

    status_code = status.HTTP_409_CONFLICT
    code = "idempotency_conflict"


class InvalidMediaSignatureError(AppError):
    """Cloudinary upload-response signature did not verify against the secret."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_media_signature"


class MediaUploadError(AppError):
    """Cloudinary returned an error or a malformed upload response."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "media_upload_failed"


# ---------- Response builders ----------


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_response(
    status_code: int,
    *,
    detail: str,
    code: str,
    request_id: str | None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "detail": detail,
        "code": code,
        "request_id": request_id,
    }
    if extra:
        body["meta"] = extra
    return JSONResponse(status_code=status_code, content=body)


# ---------- Handlers ----------


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    log.warning(
        "app_error",
        code=exc.code,
        status_code=exc.status_code,
        message=exc.message,
        path=request.url.path,
    )
    return _error_response(
        exc.status_code,
        detail=exc.message,
        code=exc.code,
        request_id=_request_id(request),
        extra=exc.extra or None,
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    return _error_response(
        exc.status_code,
        detail=str(exc.detail),
        code=f"http_{exc.status_code}",
        request_id=_request_id(request),
    )


def _safe_validation_errors(errors: list[Any]) -> list[dict[str, Any]]:
    """Pydantic error rows minus the un-JSON-serialisable `ctx.error` field."""
    out: list[dict[str, Any]] = []
    for row in errors:
        safe = {k: v for k, v in row.items() if k != "ctx"}
        ctx = row.get("ctx")
        if isinstance(ctx, dict):
            safe_ctx = {k: v for k, v in ctx.items() if k != "error"}
            if safe_ctx:
                safe["ctx"] = safe_ctx
        out.append(safe)
    return out


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return _error_response(
        422,
        detail="Request validation failed",
        code="validation_error",
        request_id=_request_id(request),
        # Sanitise — Pydantic stuffs the raw Python exception object into
        # `ctx.error` when a `@model_validator` raises ValueError. JSONResponse
        # uses stdlib `json.dumps` (no jsonable_encoder), so that object trips
        # `TypeError: not JSON serializable`. Strip non-JSON keys per row.
        extra={"errors": _safe_validation_errors(exc.errors())},
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    log.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_type=type(exc).__name__,
    )
    return _error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="An unexpected error occurred. Please retry.",
        code="internal_error",
        request_id=_request_id(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire all handlers onto the FastAPI app. Call once at app construction."""
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_exception_handler)
