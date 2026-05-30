"""Add admin-managed product code column to `products`.

Additive only. New nullable `code` column (≤80 chars) for an admin-managed
product identifier shown on the storefront PDP. Separate from variant `sku`
so variant SKUs never surface as the public product code. Existing rows get
NULL — the storefront PDP hides the code line when null.

Safe to run hot. No data backfill required.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("code", sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "code")
