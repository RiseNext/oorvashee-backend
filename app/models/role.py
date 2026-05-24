"""RBAC roles + user_roles junction.

Phase 1 launch uses two seeded roles (`customer`, `admin`). The table shape
is RBAC-ready: more roles (`staff`, `marketing`, `support`) can be added by
inserting rows + granting permissions via the existing `user_roles` join —
no schema change required.

Permissions themselves (fine-grained CAN-do-X) are intentionally NOT modeled
yet — role-name checks at the dependency layer (`require_admin`) are
sufficient for launch. Add a `permissions` + `role_permissions` table when
the matrix exceeds 3–4 roles.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system: Mapped[bool] = mapped_column(
        nullable=False, server_default="false"
    )  # seeded roles cannot be deleted via admin UI

    members: Mapped[list[UserRole]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserRole(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Many-to-many between users and roles.

    A surrogate UUID PK + a UNIQUE(user_id, role_id) constraint keeps
    audit-friendly per-row records (when added, by whom) without sacrificing
    uniqueness.
    """

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="roles", foreign_keys=[user_id]
    )
    role: Mapped[Role] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )
