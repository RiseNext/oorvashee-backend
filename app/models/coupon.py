"""Discount coupons.

Code is stored uppercase + UNIQUE; service-layer normalises on insert and
lookup so customers can type any case. CHECK constraints keep value sane
(percent ≤ 100, both ≥ 0). Usage limits tracked atomically via UPDATE … SET
usage_count = usage_count + 1 WHERE id = :id AND (usage_limit IS NULL OR
usage_count < usage_limit) RETURNING * — the WHERE clause prevents
over-redemption under concurrency without explicit locking.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    AuditableMixin,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import CouponKind, pg_enum


class Coupon(UUIDPrimaryKeyMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "coupons"

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    kind: Mapped[CouponKind] = mapped_column(
        pg_enum(CouponKind, "coupon_kind"),
        nullable=False,
    )
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_order_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default="0"
    )
    max_discount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    usage_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    per_user_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)

    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        CheckConstraint("value >= 0", name="value_non_negative"),
        CheckConstraint(
            "(kind != 'percent') OR (value <= 100)", name="percent_max_100"
        ),
        CheckConstraint("usage_count >= 0", name="usage_count_non_negative"),
        CheckConstraint(
            "(usage_limit IS NULL) OR (usage_count <= usage_limit)",
            name="usage_within_limit",
        ),
        Index("ix_coupons_active_expires", "is_active", "expires_at"),
    )
