"""Enable required Postgres extensions.

Cycle 0 baseline: pgcrypto is needed for `gen_random_uuid()` used as the
server default on every UUID primary key (see app/db/base.py).

Revision ID: 0001
Revises:
Create Date: 2026-05-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto";')


def downgrade() -> None:
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto";')
