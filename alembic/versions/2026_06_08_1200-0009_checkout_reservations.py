"""Checkout sessions + reservations layer (variant-level stock reservation).

Additive, non-destructive. Introduces an expiry-bounded reservation layer on
top of the existing order-at-checkout flow:

  - 2 native ENUM types: checkout_session_status, reservation_status
    (idempotent DO-block CREATE TYPE, exactly like migration 0002).
  - checkout_sessions: an authed user's in-flight checkout, with expiry. A user
    may have only ONE active session (ACTIVE or PAYMENT_PROCESSING) at a time,
    enforced by the partial unique index uq_checkout_sessions_one_active_per_user.
  - reservations: per-variant held quantity inside a checkout session. The
    DERIVED reserved-for-a-variant figure is
        SUM(quantity) WHERE variant_id = :v
                        AND status IN ('reserved','payment_processing')
                        AND expires_at > now()
    backed by ix_reservations_variant_status_expires.
  - orders.checkout_session_id: nullable FK (SET NULL) linking the existing
    PENDING order to the session that produced it.
  - inventory.sold_quantity: forward-only cumulative sold counter for
    analytics/admin ONLY (NOT part of availability math).

LOCKED DECISIONS honoured:
  - Stock stays variant-level. Reservations reference variant_id.
  - inventory.reserved (and its CHECK constraints) is DEPRECATED but KEPT —
    this migration does NOT touch it.
  - COD is out of scope for this layer.

Production safety: CREATE-only + ADD COLUMN only. No drops/renames of existing
objects. ENUM creation is idempotent. Downgrade reverses in exact opposite,
child-before-parent order and never touches pre-existing columns/constraints
other than the ones it added.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Enum definitions — single source of truth for this migration. Values are the
# LOWERCASE .value strings (matches repo convention + the StrEnum classes in
# app/models/enums.py + the column server_defaults below).
# ---------------------------------------------------------------------------

ENUMS: list[tuple[str, list[str]]] = [
    ("checkout_session_status", [
        "active", "payment_processing", "completed", "expired", "cancelled",
    ]),
    ("reservation_status", [
        "reserved", "payment_processing", "completed", "expired", "cancelled",
    ]),
]


def _pg_enum(name: str) -> postgresql.ENUM:
    """Reference an ENUM created separately in this migration — never auto-create."""
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Create enums (idempotent, 0002 style) ----------------------------
    for name, values in ENUMS:
        values_sql = ", ".join(f"'{v}'" for v in values)
        bind.execute(sa.text(
            f"DO $$ BEGIN "
            f"  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN "
            f"    CREATE TYPE {name} AS ENUM ({values_sql}); "
            f"  END IF; "
            f"END $$;"
        ))

    # --- 2. checkout_sessions (parent) ---------------------------------------
    op.create_table(
        "checkout_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", _pg_enum("checkout_session_status"), server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_checkout_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_checkout_sessions_user_id_users", ondelete="CASCADE"),
    )
    op.create_index("ix_checkout_sessions_user_id", "checkout_sessions", ["user_id"])
    op.create_index("ix_checkout_sessions_status_expires", "checkout_sessions", ["status", "expires_at"])
    # One live checkout per user (ACTIVE or PAYMENT_PROCESSING count as active).
    op.create_index(
        "uq_checkout_sessions_one_active_per_user", "checkout_sessions", ["user_id"], unique=True,
        postgresql_where=sa.text("status IN ('active', 'payment_processing')"),
    )

    # --- 3. reservations (child of checkout_sessions) ------------------------
    op.create_table(
        "reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("checkout_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", _pg_enum("reservation_status"), server_default="reserved", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reservations"),
        sa.ForeignKeyConstraint(
            ["checkout_session_id"], ["checkout_sessions.id"],
            name="fk_reservations_checkout_session_id_checkout_sessions", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["product_variants.id"],
            name="fk_reservations_variant_id_product_variants", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_reservations_user_id_users", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("checkout_session_id", "variant_id", name="uq_reservations_checkout_session_id"),
        sa.CheckConstraint("quantity > 0", name="ck_reservations_quantity_positive"),
    )
    # Fast derived-reserved aggregate: SUM(quantity) for a variant over active rows.
    op.create_index(
        "ix_reservations_variant_status_expires", "reservations",
        ["variant_id", "status", "expires_at"],
    )

    # --- 4. inventory.sold_quantity (additive; analytics-only) ---------------
    op.add_column(
        "inventory",
        sa.Column("sold_quantity", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_inventory_sold_quantity_non_negative", "inventory", "sold_quantity >= 0",
    )

    # --- 5. orders.checkout_session_id (additive; nullable FK, SET NULL) -----
    op.add_column(
        "orders",
        sa.Column("checkout_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_checkout_session_id_checkout_sessions",
        "orders", "checkout_sessions",
        ["checkout_session_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_orders_checkout_session_id", "orders", ["checkout_session_id"])


def downgrade() -> None:
    # Reverse exact order; children before parents; drop enums last.
    # Never touches inventory.reserved or its ck_inventory_reserved_* checks.

    # --- 5. orders.checkout_session_id ---------------------------------------
    op.drop_index("ix_orders_checkout_session_id", table_name="orders")
    op.drop_constraint("fk_orders_checkout_session_id_checkout_sessions", "orders", type_="foreignkey")
    op.drop_column("orders", "checkout_session_id")

    # --- 4. inventory.sold_quantity ------------------------------------------
    op.drop_constraint("ck_inventory_sold_quantity_non_negative", "inventory", type_="check")
    op.drop_column("inventory", "sold_quantity")

    # --- 3. reservations (child) ---------------------------------------------
    op.drop_index("ix_reservations_variant_status_expires", table_name="reservations")
    op.drop_table("reservations")

    # --- 2. checkout_sessions (parent) ---------------------------------------
    op.drop_index("uq_checkout_sessions_one_active_per_user", table_name="checkout_sessions")
    op.drop_index("ix_checkout_sessions_status_expires", table_name="checkout_sessions")
    op.drop_index("ix_checkout_sessions_user_id", table_name="checkout_sessions")
    op.drop_table("checkout_sessions")

    # --- 1. enums ------------------------------------------------------------
    for name, _ in reversed(ENUMS):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {name};"))
