"""Media metadata columns for Cloudinary-managed assets.

Additive only — safe to apply with the app running.

- `product_images`: add `format` + `bytes` (Cloudinary returns these and they're
  used for download-size diagnostics + format negotiation in delivery URLs).
- `categories`: add `image_public_id` so the asset can be deleted from
  Cloudinary when the category image changes.
- `banners`: add `image_public_id`, `mobile_image_public_id`,
  `video_public_id` matching the existing URL trio.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "product_images",
        sa.Column("format", sa.String(16), nullable=True),
    )
    op.add_column(
        "product_images",
        sa.Column("bytes", sa.Integer(), nullable=True),
    )

    op.add_column(
        "categories",
        sa.Column("image_public_id", sa.String(255), nullable=True),
    )

    op.add_column(
        "banners",
        sa.Column("image_public_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "banners",
        sa.Column("mobile_image_public_id", sa.String(255), nullable=True),
    )
    op.add_column(
        "banners",
        sa.Column("video_public_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("banners", "video_public_id")
    op.drop_column("banners", "mobile_image_public_id")
    op.drop_column("banners", "image_public_id")
    op.drop_column("categories", "image_public_id")
    op.drop_column("product_images", "bytes")
    op.drop_column("product_images", "format")
