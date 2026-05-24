"""SQLAlchemy declarative base + reusable mixins.

Every model inherits from `Base`. Cross-cutting concerns (UUID PK, timestamps,
soft delete, created_by/updated_by audit) live as mixins so model files stay
focused on the domain.

All concrete model imports happen in `app/models/__init__.py` so that Alembic
autogenerate (and `Base.metadata`) can discover the full table set.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Consistent constraint naming so Alembic produces stable migration names
# across machines and review snapshots.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Root declarative base for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class UUIDPrimaryKeyMixin:
    """UUID v4 primary key via Postgres `gen_random_uuid()` (requires pgcrypto)."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        default=uuid.uuid4,
    )


class TimestampMixin:
    """`created_at` and `updated_at` managed by the DB."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """Adds `deleted_at`. Repositories filter `deleted_at IS NULL` by default.

    Apply to entities where the row may need to be hidden from users while
    still being referenceable by historical orders/audits (categories,
    addresses, reviews, banners). Order/payment/cart rows are NEVER soft
    deleted — they progress through status enums instead.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AuditableMixin:
    """`created_by` / `updated_by` user references for admin-managed entities.

    Both nullable to allow system-driven creation (CSV imports, migrations,
    seed data). Services should set these from the request principal.
    """

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name=None),
        nullable=True,
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL", name=None),
        nullable=True,
    )


def utcnow() -> datetime:
    """Timezone-aware UTC now for service-side defaults."""
    return datetime.now(UTC)
