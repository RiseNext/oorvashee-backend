"""User identity model — mirrors Clerk user.

Created/updated by the Clerk webhook (Cycle 4). Authentication itself is
handled entirely by Clerk; this table is the local projection used for FK
relationships (orders, addresses, reviews, etc.) and admin queries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserStatus, pg_enum

if TYPE_CHECKING:
    from app.models.address import Address
    from app.models.cart import Cart
    from app.models.notification import Notification
    from app.models.order import Order
    from app.models.review import Review
    from app.models.role import UserRole
    from app.models.user_profile import UserProfile
    from app.models.wishlist import Wishlist


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )

    # --- Relationships ---
    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        foreign_keys="UserRole.user_id",  # UserRole has TWO FKs to users (user_id, assigned_by)
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    addresses: Mapped[list[Address]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    cart: Mapped[Cart | None] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    wishlist_items: Mapped[list[Wishlist]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    orders: Mapped[list[Order]] = relationship(back_populates="user")
    reviews: Mapped[list[Review]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_users_email_active", "email", postgresql_where="deleted_at IS NULL"),
    )
