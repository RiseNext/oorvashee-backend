"""Reservation — a held quantity of one variant inside a CheckoutSession.

Reservations are the authoritative source of "reserved" stock (replacing the
deprecated stored `inventory.reserved` counter). The DERIVED reserved figure
for a variant is:

    SELECT COALESCE(SUM(quantity), 0)
    FROM reservations
    WHERE variant_id = :variant_id
      AND status IN ('reserved', 'payment_processing')
      AND expires_at > now()

and `available = inventory.stock - <that sum>` (Model A — `sold_quantity` is
NOT subtracted; it is already reflected in the decremented stock).

Invariants:
  - One reservation row per (checkout_session_id, variant_id) — idempotent
    checkout reload (UNIQUE).
  - `quantity > 0` (CHECK).
  - `expires_at` is set by the service from the parent session's TTL.
  - (variant_id, status, expires_at) is indexed for the fast aggregate above.

Commerce-class row: progresses through status enums, never soft-deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ReservationStatus, pg_enum

if TYPE_CHECKING:
    from app.models.checkout_session import CheckoutSession
    from app.models.product_variant import ProductVariant
    from app.models.user import User


class Reservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reservations"

    checkout_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("checkout_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[ReservationStatus] = mapped_column(
        pg_enum(ReservationStatus, "reservation_status"),
        nullable=False,
        server_default=ReservationStatus.RESERVED.value,
    )

    # Service-managed (inherited from the parent session's TTL) — no server_default.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # --- Relationships ---
    checkout_session: Mapped[CheckoutSession] = relationship(
        back_populates="reservations"
    )
    variant: Mapped[ProductVariant] = relationship()
    user: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        # Renders uq_reservations_checkout_session_id via NAMING_CONVENTION —
        # one row per (session, variant) for idempotent checkout reload.
        UniqueConstraint("checkout_session_id", "variant_id"),
        Index(
            "ix_reservations_variant_status_expires",
            "variant_id",
            "status",
            "expires_at",
        ),
    )
