"""Pagination primitives — offset + cursor.

Two shapes are supported and both flow through the same `Page[T]` response:

- **Offset**: trivial for admin tables that want totals. `?page=1&page_size=20`.
- **Cursor (opaque)**: required for high-volume listings (catalog, search,
  account orders) where offset becomes O(N) on deep pages. The cursor is
  base64(json({"k": <last_key_value>, "id": <tiebreaker>})). Routes choose
  which `key_column` defines ordering.

Both encode into `Page.next_cursor` (offset uses `"next_offset"` semantics —
the route shapes accordingly). Keep raw integers out of public APIs so we
can change the cursor encoding later without breaking clients.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Public response shape
# ---------------------------------------------------------------------------


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    total: int | None = Field(
        default=None, description="Populated only when explicitly requested."
    )


# ---------------------------------------------------------------------------
# Offset pagination
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OffsetParams:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def offset_params(
    page: int = Query(1, ge=1, le=10_000),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> OffsetParams:
    """FastAPI dependency. Use on admin / paginated list endpoints."""
    return OffsetParams(page=page, page_size=page_size)


async def paginate_offset(
    session: AsyncSession,
    stmt: Select[Any],
    params: OffsetParams,
    *,
    include_total: bool = True,
) -> tuple[Sequence[Any], int | None]:
    """Apply LIMIT/OFFSET and optionally a separate COUNT(*) query."""
    total: int | None = None
    if include_total:
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await session.execute(count_stmt)).scalar_one())

    result = await session.execute(stmt.limit(params.page_size).offset(params.offset))
    return result.scalars().all(), total


# ---------------------------------------------------------------------------
# Cursor pagination
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CursorParams:
    cursor: str | None
    limit: int


def cursor_params(
    cursor: str | None = Query(None),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> CursorParams:
    return CursorParams(cursor=cursor, limit=limit)


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode dict to opaque base64 URL-safe string."""
    raw = json.dumps(payload, separators=(",", ":"), default=_json_default).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        return json.loads(raw.decode())
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid cursor", code="invalid_cursor") from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"unserializable cursor value: {type(value).__name__}")


async def paginate_cursor(
    session: AsyncSession,
    stmt: Select[Any],
    *,
    params: CursorParams,
    key_column: Any,
    tiebreaker_column: Any,
    descending: bool = True,
) -> tuple[list[Any], str | None]:
    """Keyset/cursor pagination on a (sortable_column, tiebreaker) pair.

    `key_column` should be deterministic and indexed (e.g. `Order.created_at`).
    `tiebreaker_column` is typically the table PK; it guarantees a strict
    total ordering even when key values collide.
    """
    direction = desc if descending else asc
    order_op = "<" if descending else ">"

    base = stmt.order_by(direction(key_column), direction(tiebreaker_column))

    if params.cursor:
        decoded = decode_cursor(params.cursor)
        last_key = decoded.get("k")
        last_id = decoded.get("id")
        if last_key is None or last_id is None:
            raise ValidationError("Cursor missing keys", code="invalid_cursor")
        base = base.where(
            (key_column.op(order_op)(last_key))
            | ((key_column == last_key) & (tiebreaker_column.op(order_op)(last_id)))
        )

    # Fetch limit + 1 to detect a next page without an extra COUNT.
    result = await session.execute(base.limit(params.limit + 1))
    rows = list(result.scalars().all())

    next_cursor: str | None = None
    if len(rows) > params.limit:
        rows = rows[: params.limit]
        last = rows[-1]
        next_cursor = encode_cursor(
            {
                "k": getattr(last, key_column.key),
                "id": getattr(last, tiebreaker_column.key),
            }
        )
    return rows, next_cursor
