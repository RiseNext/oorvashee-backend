"""Storefront video wall — admin-managed YouTube films for the `/video` page.

Each row references a single YouTube video by its canonical 11-char id (the
admin pastes a full watch / `youtu.be` / Shorts / embed URL; the service
extracts and stores the id). Videos render as a portrait reels wall that
autoplays muted, loops, and exposes no controls. An optional `link_url` turns
a card into a "shop this look" tap target.

Admin orders the wall via `display_order`; `is_active` hides a video without
deleting it. Soft-deleted (removed) rows are kept for audit references.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    AuditableMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Video(
    UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, AuditableMixin, Base
):
    __tablename__ = "videos"

    # Canonical YouTube id (the 11-char value after `watch?v=` / `youtu.be/` /
    # `shorts/`). Stored — not the raw URL — so the frontend embed is built
    # deterministically and arbitrary-iframe injection is impossible.
    youtube_id: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # Optional "shop this look" destination (product/collection/any URL).
    link_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    display_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        Index(
            "ix_videos_active_order",
            "is_active",
            "display_order",
            postgresql_where="deleted_at IS NULL",
        ),
    )
