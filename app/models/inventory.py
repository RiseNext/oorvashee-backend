"""Inventory tracking — separated from `product_variants` for clean future expansion.

For Phase 1 there is exactly one `inventory` row per variant (single virtual
warehouse). The shape is multi-warehouse-ready: add a `warehouse_id` column
and unique on (variant_id, warehouse_id) when needed — no read-path code
changes.

Every mutation to `inventory.stock` MUST write a `stock_movements` row, via
the inventory_service. This append-only audit is the only authoritative
explanation for any stock figure on a dispute.

Concurrency: stock decrements MUST use `SELECT ... FOR UPDATE` on the
inventory row. Documented in INVENTORY_FLOW.md §9.
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
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import StockMovementReason, pg_enum

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.product_variant import ProductVariant
    from app.models.user import User


class Inventory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "inventory"

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one row per variant in Phase 1
    )

    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )  # reserved by in-flight orders (Phase 2 feature; column lets us add it without migration)
    low_stock_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    # Cumulative units sold, forward-only; incremented on confirmed payment.
    # ANALYTICS/admin "Sold" column ONLY — NOT part of availability math
    # (available = stock - active reservations; see reservation.py).
    sold_quantity: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    variant: Mapped[ProductVariant] = relationship(back_populates="inventory")

    __table_args__ = (
        CheckConstraint("stock >= 0", name="stock_non_negative"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        CheckConstraint("reserved <= stock", name="reserved_le_stock"),
        CheckConstraint("sold_quantity >= 0", name="sold_quantity_non_negative"),
        Index("ix_inventory_low_stock", "stock", "low_stock_threshold"),
    )


class StockMovement(UUIDPrimaryKeyMixin, Base):
    """Append-only audit of every change to `inventory.stock`.

    NOTE: deliberately does not use `TimestampMixin` — there is no `updated_at`
    because the row is immutable once written.
    """

    __tablename__ = "stock_movements"

    variant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=False,
    )
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[StockMovementReason] = mapped_column(
        pg_enum(StockMovementReason, "stock_movement_reason"),
        nullable=False,
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    variant: Mapped[ProductVariant] = relationship()
    order: Mapped[Order | None] = relationship()
    actor: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("ix_stock_movements_variant_created", "variant_id", "created_at"),
        Index("ix_stock_movements_order", "order_id"),
    )
