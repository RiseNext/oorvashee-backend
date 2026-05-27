"""Bulk-import job tracking + new enums.

Additive only. Creates two enum types (`import_kind`, `import_status`)
and the `import_jobs` table. Safe to run hot.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE import_kind AS ENUM ('product', 'variant', 'inventory');"
    )
    op.execute(
        "CREATE TYPE import_status AS ENUM "
        "('queued', 'running', 'completed', 'failed');"
    )

    op.create_table(
        "import_jobs",
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
        sa.Column(
            "kind",
            sa.dialects.postgresql.ENUM(
                "product", "variant", "inventory",
                name="import_kind", create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.dialects.postgresql.ENUM(
                "queued", "running", "completed", "failed",
                name="import_status", create_type=False,
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rows_total", sa.Integer(), nullable=True),
        sa.Column(
            "rows_processed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "rows_succeeded", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "rows_failed", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "errors",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("request_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_import_jobs_kind_status", "import_jobs", ["kind", "status"]
    )
    op.create_index("ix_import_jobs_created", "import_jobs", ["created_at"])
    op.create_index(
        "ix_import_jobs_created_by", "import_jobs", ["created_by"]
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_created_by", table_name="import_jobs")
    op.drop_index("ix_import_jobs_created", table_name="import_jobs")
    op.drop_index("ix_import_jobs_kind_status", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.execute("DROP TYPE IF EXISTS import_status;")
    op.execute("DROP TYPE IF EXISTS import_kind;")
