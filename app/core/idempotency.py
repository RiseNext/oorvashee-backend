"""Idempotency-Key handling for write endpoints.

Pattern:
- Client sends `Idempotency-Key: <opaque-token>` on POST.
- Server hashes the request body. If `(key, route)` exists:
    - same body hash → replay the cached response (same status + body).
    - different body hash → 409 IdempotencyConflictError.
- Otherwise, execute, capture the response, and persist it.

For the keys themselves, recommend UUIDv4 from the frontend per submit.
TTL is 24 hours (enforced by a cleanup job — Phase 2G).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy import select

from app.core.exceptions import IdempotencyConflictError, ValidationError
from app.core.logging import get_logger
from app.db.deps import DbSession
from app.models.request_idempotency import RequestIdempotency

log = get_logger(__name__)

_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _hash_body(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class IdempotencyContext:
    key: str
    route: str
    request_hash: str
    cached_status: int | None
    cached_body: dict[str, Any] | None

    @property
    def is_replay(self) -> bool:
        return self.cached_status is not None


async def idempotency_required(
    request: Request,
    session: DbSession,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", convert_underscores=False)
    ] = None,
) -> IdempotencyContext:
    """Mandatory Idempotency-Key dependency.

    Use on POSTs whose double-execution would be expensive or unsafe
    (`POST /checkout/orders`, `POST /payments/verify`).
    """
    if not idempotency_key:
        raise ValidationError(
            "Idempotency-Key header is required for this endpoint",
            code="idempotency_key_missing",
        )
    if not _KEY_PATTERN.match(idempotency_key):
        raise ValidationError(
            "Idempotency-Key must be 8-128 chars of [A-Za-z0-9_-]",
            code="idempotency_key_invalid",
        )

    body = await request.body()
    body_hash = _hash_body(body)
    route = f"{request.method}:{request.url.path}"

    existing = (
        await session.execute(
            select(RequestIdempotency).where(
                RequestIdempotency.key == idempotency_key,
                RequestIdempotency.route == route,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        return IdempotencyContext(
            key=idempotency_key,
            route=route,
            request_hash=body_hash,
            cached_status=None,
            cached_body=None,
        )

    if existing.request_hash != body_hash:
        log.warning(
            "idempotency_body_mismatch",
            key=idempotency_key,
            route=route,
        )
        raise IdempotencyConflictError(
            "Idempotency-Key was previously used with a different request body",
        )

    log.info("idempotency_replay", key=idempotency_key, route=route)
    return IdempotencyContext(
        key=idempotency_key,
        route=route,
        request_hash=body_hash,
        cached_status=existing.response_status,
        cached_body=existing.response_body,
    )


async def with_idempotency(
    *,
    ctx: IdempotencyContext,
    session: DbSession,
    user_id: Any | None,
    status_code: int,
    execute: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[int, dict[str, Any]]:
    """Run `execute()` if not a replay, then cache + return the result.

    Returns `(status_code, body)`. Routers wrap their normal logic in a
    closure passed as `execute`.
    """
    if ctx.is_replay:
        return ctx.cached_status or 200, ctx.cached_body or {}

    body = await execute()
    # Coerce to JSON-safe shape (Decimal → str via custom encoder)
    json_safe = json.loads(json.dumps(body, default=str))

    session.add(
        RequestIdempotency(
            key=ctx.key,
            route=ctx.route,
            user_id=user_id,
            request_hash=ctx.request_hash,
            response_status=status_code,
            response_body=json_safe,
        )
    )
    await session.flush()
    return status_code, json_safe


IdempotencyDep = Annotated[IdempotencyContext, Depends(idempotency_required)]
