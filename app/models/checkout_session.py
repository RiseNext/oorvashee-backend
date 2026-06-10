"""CheckoutSession — an authed user's in-flight, expiry-bounded checkout.

A session is created when an authenticated user enters checkout. It owns a set
of `reservations` (one per variant) that hold stock for the duration of the
session. The existing PENDING order is still created at checkout (the
"keep order-at-checkout" decision) and links back via
`orders.checkout_session_id`.

Invariants:
  - Authed-only: `user_id` is NOT NULL.
  - ONE active session per user: a user may have at most one session whose
    status is ACTIVE or PAYMENT_PROCESSING, enforced by the partial unique
    index `uq_checkout_sessions_one_active_per_user`. The checkout service
    reuses an existing active/processing session (refreshing reservations +
    expires_at) instead of creating a second one. COMPLETED / EXPIRED /
    CANCELLED sessions do NOT count as active and free the user to start anew.
  - `expires_at` is set by the service (utcnow() + TTL), NOT by the DB.
  - Status progresses ACTIVE -> PAYMENT_PROCESSING -> COMPLETED, or is swept to
    EXPIRED by the advisory-lock-guarded cron, or CANCELLED by the user.
  - (status, expires_at) is indexed for the expiry sweep.

Commerce-class row: progresses through status enums, never soft-deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CheckoutSessionStatus, pg_enum

if TYPE_CHECKING:
    from app.models.reservation import Reservation
    from app.models.user import User


class CheckoutSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "checkout_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[CheckoutSessionStatus] = mapped_column(
        pg_enum(CheckoutSessionStatus, "checkout_session_status"),
        nullable=False,
        server_default=CheckoutSessionStatus.ACTIVE.value,
    )

    # Service-managed (utcnow() + TTL) — no server_default.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # --- Relationships ---
    user: Mapped[User] = relationship()
    reservations: Mapped[list[Reservation]] = relationship(
        back_populates="checkout_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_checkout_sessions_user_id", "user_id"),
        Index("ix_checkout_sessions_status_expires", "status", "expires_at"),
        # One live checkout per user: ACTIVE + PAYMENT_PROCESSING are "active".
        Index(
            "uq_checkout_sessions_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=text(
                "status IN ('active', 'payment_processing')"
            ),
        ),
    )
