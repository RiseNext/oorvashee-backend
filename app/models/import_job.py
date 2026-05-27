"""Bulk-import job tracking.

One row per `POST /api/v1/admin/imports/*` upload. The row outlives the
HTTP request — BackgroundTasks-driven processing updates it as it goes,
and the admin polls `GET /admin/imports/{job_id}` to watch progress.

Errors per row are stored in `errors` (JSONB) capped at 1000 entries so a
runaway CSV can't bloat a single row. The summary text is plain admin-
readable; the `metadata_` JSON carries machine-readable detail
(filename, dry_run flag, chunk counts).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ImportKind, ImportStatus, pg_enum


class ImportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"

    kind: Mapped[ImportKind] = mapped_column(
        pg_enum(ImportKind, "import_kind"), nullable=False
    )
    status: Mapped[ImportStatus] = mapped_column(
        pg_enum(ImportStatus, "import_status"),
        nullable=False,
        server_default=ImportStatus.QUEUED.value,
    )

    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dry_run: Mapped[bool] = mapped_column(nullable=False, server_default="false")

    rows_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rows_processed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    rows_succeeded: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    rows_failed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # `[{row_number: int, errors: {field: msg}}, ...]` — capped at 1000.
    errors: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_import_jobs_kind_status", "kind", "status"),
        Index("ix_import_jobs_created", "created_at"),
        Index("ix_import_jobs_created_by", "created_by"),
    )
