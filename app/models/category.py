"""Category taxonomy + product↔category junction.

`kind` segregates the same table into multiple filter axes (fabric, occasion,
region, price-bracket, color, free-form collection). Self-referencing
`parent_id` lets us nest e.g. `region → south → tamil-nadu` later without
adding new tables.

Slugs are immutable once published (frontend filter URLs depend on them).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    AuditableMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import CategoryKind, pg_enum

if TYPE_CHECKING:
    from app.models.product import Product


class Category(
    UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, AuditableMixin, Base
):
    __tablename__ = "categories"

    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[CategoryKind] = mapped_column(
        pg_enum(CategoryKind, "category_kind"),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    seo_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Relationships ---
    parent: Mapped[Category | None] = relationship(
        remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list[Category]] = relationship(back_populates="parent")

    product_links: Mapped[list[ProductCategory]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_categories_kind_active", "kind", "is_active"),
        Index("ix_categories_parent", "parent_id"),
    )


class ProductCategory(Base):
    """Pure junction — no surrogate PK; the (product, category) pair is the PK."""

    __tablename__ = "product_categories"

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    )

    product: Mapped[Product] = relationship(back_populates="category_links")
    category: Mapped[Category] = relationship(back_populates="product_links")

    __table_args__ = (
        UniqueConstraint("product_id", "category_id", name="uq_product_categories_pair"),
        Index("ix_product_categories_category", "category_id"),
    )
