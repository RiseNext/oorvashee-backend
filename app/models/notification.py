"""User notifications — channel-aware (email / SMS / WhatsApp / in-app).

For Phase 1, the only writer is the order pipeline (confirmation +
status-change). The shape is general enough to absorb marketing, review
reminders, and admin alerts later without a schema change.

Background workers (Cycle 6+ when Arq lands) read `status='queued'`,
attempt delivery, and update to `sent` or `failed` with `failure_reason`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NotificationChannel, NotificationStatus, pg_enum

if TYPE_CHECKING:
    from app.models.user import User


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )

    # For guest order notifications (no user row) we record the address directly.
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(
        pg_enum(NotificationChannel, "notification_channel"),
        nullable=False,
    )

    template_key: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    status: Mapped[NotificationStatus] = mapped_column(
        pg_enum(NotificationStatus, "notification_status"),
        nullable=False,
        server_default=NotificationStatus.QUEUED.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User | None] = relationship(back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_user_created", "user_id", "created_at"),
        Index(
            "ix_notifications_queued",
            "scheduled_for",
            postgresql_where="status = 'queued'",
        ),
        Index("ix_notifications_channel_status", "channel", "status"),
    )
