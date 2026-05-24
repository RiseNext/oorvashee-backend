"""Cloudinary-hosted product images.

Backend never proxies bytes — admin frontend uploads directly to Cloudinary
using a signature obtained from `POST /admin/media/sign`. This table stores
the resulting `public_id` (for deletion/transformation) and a delivery URL.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.product import Product


class ProductImage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "product_images"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )

    cloudinary_public_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    product: Mapped[Product] = relationship(back_populates="images")

    __table_args__ = (
        Index("ix_product_images_product_position", "product_id", "position"),
        Index(
            "uq_product_images_primary_per_product",
            "product_id",
            unique=True,
            postgresql_where="is_primary = true",
        ),
    )
