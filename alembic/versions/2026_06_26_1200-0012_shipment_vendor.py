"""Add Daakia courier-vendor columns to shipments.

Additive, non-destructive. The courier selects which Daakia carrier (vendor) a
shipment is on when entering the AWB; both the vendor id and the AWB are required
for Daakia's track-shipment call. Columns are NULLABLE — existing shipments (and
manual admin shipments) have neither until re-saved on the /courier page.

Safe to run hot. No data migration.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "shipments",
        sa.Column("courier_vendor_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "shipments",
        sa.Column("courier_vendor_name", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("shipments", "courier_vendor_name")
    op.drop_column("shipments", "courier_vendor_id")
