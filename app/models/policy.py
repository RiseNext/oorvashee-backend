"""Store policy documents — admin-managed informational copy.

A small fixed-set table holding the store-wide legal / info docs (privacy,
shipping, refund, terms). Each row is keyed by a stable `key` that the
storefront uses to slot the doc into the right accordion row / page.

Fixed for every product: products do NOT carry their own policy text. The
storefront renders these rows identically under every product, in the cart,
and at checkout — editing one row here updates all of them at once. The set is
seeded by migration and managed (edited / toggled) by admins; rows are not
created or deleted at runtime, so there is no soft-delete here.

`body` is free text; paragraphs are separated by blank lines and split for
display client-side.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    AuditableMixin,
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Policy(UUIDPrimaryKeyMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "policies"

    # Stable storefront contract (privacy | shipping | refund | terms).
    # Immutable once seeded — the frontend keys pages/accordion rows off it.
    key: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(400), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Order in the accordion / overview list. is_active hides a doc without
    # deleting it (admin toggle).
    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (UniqueConstraint("key", name="uq_policies_key"),)
