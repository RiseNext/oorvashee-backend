"""Product variants — the unit of price + stock.

A product has at minimum one variant (the "default"). Per PRD §6.1, variants
differ by color / fabric (other axes addable via the JSONB `attributes`
column without a schema change).

Stock lives in a separate `inventory` table — keyed by variant — to keep the
hot read path (variant attributes + price) decoupled from the hot write path
(stock decrements) and to allow multi-warehouse expansion later.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.inventory import Inventory
    from app.models.product import Product


class ProductVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_variants"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    sku: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    color: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fabric: Mapped[str | None] = mapped_column(String(80), nullable=True)
    size: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Catch-all for additional axes (length, blouse-included, weave-style, ...)
    # so introducing a new variant axis later doesn't require a migration.
    attributes: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    price_override: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    weight_grams: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    product: Mapped[Product] = relationship(back_populates="variants")
    inventory: Mapped[Inventory | None] = relationship(
        back_populates="variant",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_product_variants_product", "product_id"),
        Index(
            "uq_product_variants_default_per_product",
            "product_id",
            unique=True,
            postgresql_where="is_default = true",
        ),
    )
