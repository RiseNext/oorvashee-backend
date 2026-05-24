"""Promotional banners — placement-aware homepage / category / cart slots.

Each banner targets a `placement` (homepage_hero, category_top, etc.) and is
filtered by activation window + active flag. Admin orders banners via
`display_order`.

Click attribution and impression tracking are out of scope for Phase 1 —
when added, they go in a separate `banner_events` table (append-only).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    AuditableMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import BannerPlacement, pg_enum

if TYPE_CHECKING:
    from app.models.category import Category
    from app.models.product import Product


class Banner(
    UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, AuditableMixin, Base
):
    __tablename__ = "banners"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)

    placement: Mapped[BannerPlacement] = mapped_column(
        pg_enum(BannerPlacement, "banner_placement"),
        nullable=False,
    )

    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mobile_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mobile_image_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_public_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    cta_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cta_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional links — surface a banner that promotes a specific product/category.
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    product: Mapped[Product | None] = relationship()
    category: Mapped[Category | None] = relationship()

    __table_args__ = (
        Index(
            "ix_banners_placement_active",
            "placement",
            "is_active",
            postgresql_where="deleted_at IS NULL",
        ),
        Index("ix_banners_window", "starts_at", "ends_at"),
    )
