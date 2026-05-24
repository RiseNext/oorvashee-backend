"""Add request_idempotency table for Idempotency-Key middleware.

CREATE-only. Safe to apply on a live DB with no downtime.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "request_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("route", sa.String(255), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_request_idempotency"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_request_idempotency_user_id_users",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("key", "route", name="uq_request_idempotency_key_route"),
    )
    op.create_index("ix_request_idempotency_created", "request_idempotency", ["created_at"])
    op.create_index("ix_request_idempotency_user", "request_idempotency", ["user_id"])


def downgrade() -> None:
    op.drop_table("request_idempotency")
