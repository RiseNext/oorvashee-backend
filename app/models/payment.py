"""Razorpay payment record + Shipment.

One order may have multiple `payments` rows (failed → retried), but only the
latest captured one drives `order.payment_status`. Razorpay webhook
idempotency is enforced via UNIQUE on `webhook_event_id`.

Shipment is 1:1 with order in Phase 1. Splits/partial shipments (Phase 2+)
would replace this with a many-to-many through `shipment_items`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import RazorpayPaymentStatus, pg_enum

if TYPE_CHECKING:
    from app.models.order import Order


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )

    razorpay_order_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    razorpay_payment_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    razorpay_signature: Mapped[str | None] = mapped_column(String(255), nullable=True)
    webhook_event_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )

    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    status: Mapped[RazorpayPaymentStatus] = mapped_column(
        pg_enum(RazorpayPaymentStatus, "razorpay_payment_status"),
        nullable=False,
    )
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship(back_populates="payments")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        Index("ix_payments_order", "order_id"),
        Index("ix_payments_status", "status"),
    )


class Shipment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "shipments"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    courier_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tracking_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship(back_populates="shipment")

    __table_args__ = (
        Index("ix_shipments_tracking_id", "tracking_id"),
    )
