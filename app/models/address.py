"""Saved customer addresses.

One user → many addresses. Soft-deleted (deleted_at) so a historical order's
shipping address reference can survive a customer removing the entry from
their saved list. Order snapshots also embed the address as JSONB at
checkout time for full historical accuracy.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Address(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "addresses"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(120), nullable=False)
    postal_code: Mapped[str] = mapped_column(String(16), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, server_default="IN")
    is_default: Mapped[bool] = mapped_column(nullable=False, server_default="false")

    user: Mapped[User] = relationship(back_populates="addresses")

    __table_args__ = (
        Index("ix_addresses_user", "user_id"),
        # Only ONE default address per active user.
        Index(
            "uq_addresses_user_default",
            "user_id",
            unique=True,
            postgresql_where="is_default = true AND deleted_at IS NULL",
        ),
    )
