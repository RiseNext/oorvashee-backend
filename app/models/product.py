"""Core catalog model.

Critical invariants (per PRD §7.2):
- `slug` is generated at creation, immutable after publish — the bot URL
  contract depends on this. Enforced at the service layer; the column itself
  is UNIQUE NOT NULL so a buggy bypass still trips at the DB.
- `status='archived'` is the only "delete" — `/products/{slug}` returns
  `available=false` instead of 404, preserving shared URLs.

Search:
- `search_vector` is a TSVECTOR column maintained by a BEFORE INSERT/UPDATE
  trigger (`products_search_vector_trg`, defined in migration 0002).
  GIN-indexed for `plainto_tsquery` queries. The trigger derives the value
  from name + short_description + description + tags so application code
  never sets it manually.
- A generated column would be tidier but Postgres rejects the expression
  as non-IMMUTABLE (the implicit text→regconfig cast on the 'english' arg
  is STABLE). Triggers are the official Postgres FTS recommendation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    AuditableMixin,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.enums import ProductStatus, pg_enum

if TYPE_CHECKING:
    from app.models.category import ProductCategory
    from app.models.product_image import ProductImage
    from app.models.product_variant import ProductVariant
    from app.models.review import Review


class Product(UUIDPrimaryKeyMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "products"

    slug: Mapped[str] = mapped_column(String(220), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Admin-managed product code shown on the storefront PDP (e.g. "OOR-SAR-001").
    # Separate from variant `sku` — surfaced as the public product identifier so
    # variant SKUs never leak into customer-facing UI.
    code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    mrp: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    status: Mapped[ProductStatus] = mapped_column(
        pg_enum(ProductStatus, "product_status"),
        nullable=False,
        server_default=ProductStatus.DRAFT.value,
    )

    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    featured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_bestseller: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_new: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    seo_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # DB-managed FTS column — populated by `products_search_vector_trg`
    # (BEFORE INSERT OR UPDATE OF name, short_description, description, tags).
    # Application code must NEVER write to this column; the trigger overwrites
    # any value you set on INSERT/UPDATE.
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR, nullable=True)

    # --- Relationships ---
    images: Mapped[list[ProductImage]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProductImage.position",
    )
    variants: Mapped[list[ProductVariant]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    category_links: Mapped[list[ProductCategory]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reviews: Mapped[list[Review]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_products_status", "status"),
        Index(
            "ix_products_featured",
            "featured",
            postgresql_where="featured = true AND status = 'published'",
        ),
        Index("ix_products_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_products_tags", "tags", postgresql_using="gin"),
        Index("ix_products_published_at", "published_at"),
    )
