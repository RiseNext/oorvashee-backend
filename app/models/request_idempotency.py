"""Idempotency-Key storage.

Backs the `Idempotency-Key` HTTP header on write endpoints (currently
`POST /checkout/orders` and `POST /payments/verify`). The middleware
in `app/core/idempotency.py` uses this table to detect and replay
previously-processed requests.

Rows are scoped to (key, route, user_id) — the same key on a different
route or by a different user is treated as a distinct request.

A daily cleanup job (Phase 2G) deletes rows older than 24 hours.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class RequestIdempotency(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "request_idempotency"

    key: Mapped[str] = mapped_column(String(128), nullable=False)
    route: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Hash of the request body — defends against the same key being reused
    # with different inputs (we 409 in that case rather than replay a stale
    # response that doesn't match what the caller now expects).
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # `(key, route)` is the natural primary lookup key. Same key on
        # different route is a different request. user_id is informational.
        UniqueConstraint("key", "route", name="uq_request_idempotency_key_route"),
        Index("ix_request_idempotency_created", "created_at"),
        Index("ix_request_idempotency_user", "user_id"),
    )
