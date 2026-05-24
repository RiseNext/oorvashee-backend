"""User wishlist — association between user and product."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.user import User


class Wishlist(UUIDPrimaryKeyMixin, Base):
    """Surrogate-PK association so we can audit when an item was added.

    Uniqueness is enforced by (user_id, product_id).
    """

    __tablename__ = "wishlists"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="wishlist_items")
    product: Mapped[Product] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_wishlists_user_product"),
        Index("ix_wishlists_user_added", "user_id", "added_at"),
    )
