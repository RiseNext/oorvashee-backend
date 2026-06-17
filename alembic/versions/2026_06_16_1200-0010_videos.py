"""Storefront video wall table.

Additive only. Creates the `videos` table that backs the admin-managed,
autoplaying reels wall on the `/video` page. No seed — the wall starts empty
and admins add YouTube films from the dashboard.

Safe to run hot.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("youtube_id", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=True),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column(
            "display_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Matches SoftDeleteMixin's indexed `deleted_at` + the model's activation
    # index (active videos in display order, deleted rows excluded).
    op.create_index("ix_videos_deleted_at", "videos", ["deleted_at"])
    op.create_index(
        "ix_videos_active_order",
        "videos",
        ["is_active", "display_order"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_videos_active_order", table_name="videos")
    op.drop_index("ix_videos_deleted_at", table_name="videos")
    op.drop_table("videos")
