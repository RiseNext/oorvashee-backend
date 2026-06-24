"""Seed the `courier` RBAC role.

Additive, DATA-ONLY (no schema change). Inserts the `courier` system role used
by the delivery/dispatch portal. Authorization itself reads the Clerk JWT
`public_metadata.role` claim (see app/core/security.py::require_role), so the
role works the moment an admin sets a user's Clerk role to "courier"; this row
keeps the local `roles` / `user_roles` mirror complete for the Clerk webhook
sync (UserSyncService) and the admin/audit views.

Idempotent via `ON CONFLICT (name) DO NOTHING` (mirrors the role seed in 0002),
so it is safe to run hot and safe to re-run.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-24
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO roles (name, description, is_system)
        VALUES
          ('courier',
           'Delivery partner: dispatch list + AWB entry only. No admin, payment, or PII access.',
           true)
        ON CONFLICT (name) DO NOTHING;
        """
    )


def downgrade() -> None:
    # FK user_roles.role_id -> roles.id is ON DELETE CASCADE, so removing the
    # seeded role also clears any courier assignments.
    op.execute("DELETE FROM roles WHERE name = 'courier';")
