"""Initial domain schema — all Phase 1 tables.

Creates 14 native ENUM types + 24 tables + every index/constraint declared
on the models. Hand-written (not autogenerate) so:
  - ENUM creation is ordered before the tables that reference them
  - FK ordering matches table-creation ordering
  - the migration is deterministic and reviewable

Production safety:
  - All operations are CREATE-only — no data loss possible.
  - `IF NOT EXISTS` on ENUM types so re-applying after a failed partial run
    is safe.
  - Downgrade drops everything; never run downgrade against a DB with real
    customer data.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Enum definitions — single source of truth for the migration.
# Tables below reference these by name via `postgresql.ENUM(name=..., create_type=False)`.
# ---------------------------------------------------------------------------

ENUMS: list[tuple[str, list[str]]] = [
    ("user_status", ["active", "disabled"]),
    ("category_kind", ["fabric", "occasion", "region", "price_bracket", "color", "collection"]),
    ("product_status", ["draft", "published", "unavailable", "archived"]),
    ("order_status", ["placed", "packed", "shipped", "delivered", "cancelled"]),
    ("payment_status", ["pending", "paid", "failed", "refunded", "cod_pending"]),
    ("payment_method", ["razorpay", "cod"]),
    ("razorpay_payment_status", ["created", "authorized", "captured", "failed", "refunded"]),
    ("coupon_kind", ["percent", "flat"]),
    ("stock_movement_reason", [
        "order_placed", "order_cancelled", "manual_adjustment", "csv_import", "restock"
    ]),
    ("review_status", ["pending", "approved", "rejected"]),
    ("banner_placement", [
        "homepage_hero", "homepage_secondary", "category_top", "cart_promo"
    ]),
    ("notification_channel", ["email", "sms", "whatsapp", "in_app"]),
    ("notification_status", ["queued", "sent", "failed", "read"]),
    ("audit_action", [
        "create", "update", "delete", "login", "logout",
        "payment_captured", "stock_adjusted", "status_changed",
    ]),
]


def _pg_enum(name: str) -> postgresql.ENUM:
    """Reference an ENUM that was created separately — never auto-create."""
    return postgresql.ENUM(name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. Create enums (idempotent) ----------------------------------------
    for name, values in ENUMS:
        values_sql = ", ".join(f"'{v}'" for v in values)
        bind.execute(sa.text(
            f"DO $$ BEGIN "
            f"  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN "
            f"    CREATE TYPE {name} AS ENUM ({values_sql}); "
            f"  END IF; "
            f"END $$;"
        ))

    # --- 2. roles (independent) ----------------------------------------------
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    # --- 3. users (independent) ----------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clerk_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("status", _pg_enum("user_status"), server_default="active", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("clerk_user_id", name="uq_users_clerk_user_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])
    op.create_index(
        "ix_users_email_active", "users", ["email"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- 4. user_profiles ----------------------------------------------------
    op.create_table(
        "user_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("gender", sa.String(32), nullable=True),
        sa.Column("locale", sa.String(16), server_default="en-IN", nullable=False),
        sa.Column("preferences", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_profiles"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_profiles_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_user_profiles_user_id"),
    )

    # --- 5. user_roles -------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assigned_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user_roles"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_roles_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_user_roles_role_id_roles", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], name="fk_user_roles_assigned_by_users", ondelete="SET NULL"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )

    # --- 6. addresses --------------------------------------------------------
    op.create_table(
        "addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("recipient_name", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("line1", sa.String(255), nullable=False),
        sa.Column("line2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("state", sa.String(120), nullable=False),
        sa.Column("postal_code", sa.String(16), nullable=False),
        sa.Column("country", sa.String(2), server_default="IN", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_addresses"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_addresses_user_id_users", ondelete="CASCADE"),
    )
    op.create_index("ix_addresses_deleted_at", "addresses", ["deleted_at"])
    op.create_index("ix_addresses_user", "addresses", ["user_id"])
    op.create_index(
        "uq_addresses_user_default", "addresses", ["user_id"], unique=True,
        postgresql_where=sa.text("is_default = true AND deleted_at IS NULL"),
    )

    # --- 7. categories (self-referencing) ------------------------------------
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.String(160), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", _pg_enum("category_kind"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("seo_title", sa.String(255), nullable=True),
        sa.Column("seo_description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_categories"),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], name="fk_categories_parent_id_categories", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_categories_created_by_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_categories_updated_by_users", ondelete="SET NULL"),
        sa.UniqueConstraint("slug", name="uq_categories_slug"),
    )
    op.create_index("ix_categories_deleted_at", "categories", ["deleted_at"])
    op.create_index("ix_categories_kind_active", "categories", ["kind", "is_active"])
    op.create_index("ix_categories_parent", "categories", ["parent_id"])

    # --- 8. products ---------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.String(220), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("short_description", sa.String(500), nullable=True),
        sa.Column("base_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("mrp", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("status", _pg_enum("product_status"), server_default="draft", nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False),
        sa.Column("featured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_bestseller", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_new", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("seo_title", sa.String(255), nullable=True),
        sa.Column("seo_description", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        # search_vector is maintained by `products_search_vector_trg`
        # (created right after this table). Postgres rejects this as a
        # GENERATED column because `to_tsvector(text, text)` resolves the
        # 'english' arg via an implicit text→regconfig cast and is therefore
        # not IMMUTABLE.
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_products_created_by_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_products_updated_by_users", ondelete="SET NULL"),
        sa.UniqueConstraint("slug", name="uq_products_slug"),
    )
    # --- search_vector trigger (replaces the rejected generated column) ----
    # `OR REPLACE` makes the function idempotent on re-run.
    # `CREATE OR REPLACE TRIGGER` requires Postgres 14+ (Neon ≥15, fine).
    bind.execute(sa.text(
        """
        CREATE OR REPLACE FUNCTION products_search_vector_update()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english'::regconfig, coalesce(NEW.name, '')),              'A') ||
                setweight(to_tsvector('english'::regconfig, coalesce(NEW.short_description, '')), 'B') ||
                setweight(to_tsvector('english'::regconfig, coalesce(NEW.description, '')),       'C') ||
                setweight(
                    to_tsvector(
                        'english'::regconfig,
                        coalesce(array_to_string(NEW.tags, ' '), '')
                    ),
                    'C'
                );
            RETURN NEW;
        END
        $$;
        """
    ))
    bind.execute(sa.text(
        """
        CREATE OR REPLACE TRIGGER products_search_vector_trg
        BEFORE INSERT OR UPDATE OF name, short_description, description, tags
        ON products
        FOR EACH ROW EXECUTE FUNCTION products_search_vector_update();
        """
    ))
    # Backfill any rows that pre-date the trigger. No-op on a fresh DB;
    # cheap insurance for re-runs / repaired prior partial migrations.
    bind.execute(sa.text(
        """
        UPDATE products
        SET search_vector =
            setweight(to_tsvector('english'::regconfig, coalesce(name, '')),              'A') ||
            setweight(to_tsvector('english'::regconfig, coalesce(short_description, '')), 'B') ||
            setweight(to_tsvector('english'::regconfig, coalesce(description, '')),       'C') ||
            setweight(
                to_tsvector('english'::regconfig, coalesce(array_to_string(tags, ' '), '')),
                'C'
            )
        WHERE search_vector IS NULL;
        """
    ))

    op.create_index("ix_products_status", "products", ["status"])
    op.create_index(
        "ix_products_featured", "products", ["featured"],
        postgresql_where=sa.text("featured = true AND status = 'published'"),
    )
    op.create_index("ix_products_search_vector", "products", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_products_tags", "products", ["tags"], postgresql_using="gin")
    op.create_index("ix_products_published_at", "products", ["published_at"])

    # --- 9. product_variants -------------------------------------------------
    op.create_table(
        "product_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("color", sa.String(80), nullable=True),
        sa.Column("fabric", sa.String(80), nullable=True),
        sa.Column("size", sa.String(40), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("price_override", sa.Numeric(12, 2), nullable=True),
        sa.Column("weight_grams", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_product_variants"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_variants_product_id_products", ondelete="CASCADE"),
        sa.UniqueConstraint("sku", name="uq_product_variants_sku"),
    )
    op.create_index("ix_product_variants_product", "product_variants", ["product_id"])
    op.create_index(
        "uq_product_variants_default_per_product", "product_variants", ["product_id"], unique=True,
        postgresql_where=sa.text("is_default = true"),
    )

    # --- 10. product_images --------------------------------------------------
    op.create_table(
        "product_images",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cloudinary_public_id", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("alt_text", sa.String(255), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_product_images"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_images_product_id_products", ondelete="CASCADE"),
    )
    op.create_index("ix_product_images_product_position", "product_images", ["product_id", "position"])
    op.create_index(
        "uq_product_images_primary_per_product", "product_images", ["product_id"], unique=True,
        postgresql_where=sa.text("is_primary = true"),
    )

    # --- 11. inventory -------------------------------------------------------
    op.create_table(
        "inventory",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stock", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
        sa.Column("low_stock_threshold", sa.Integer(), server_default="2", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_inventory"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], name="fk_inventory_variant_id_product_variants", ondelete="CASCADE"),
        sa.UniqueConstraint("variant_id", name="uq_inventory_variant_id"),
        sa.CheckConstraint("stock >= 0", name="ck_inventory_stock_non_negative"),
        sa.CheckConstraint("reserved >= 0", name="ck_inventory_reserved_non_negative"),
        sa.CheckConstraint("reserved <= stock", name="ck_inventory_reserved_le_stock"),
    )
    op.create_index("ix_inventory_low_stock", "inventory", ["stock", "low_stock_threshold"])

    # --- 12. product_categories (junction) -----------------------------------
    op.create_table(
        "product_categories",
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("product_id", "category_id", name="pk_product_categories"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_categories_product_id_products", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="fk_product_categories_category_id_categories", ondelete="CASCADE"),
        sa.UniqueConstraint("product_id", "category_id", name="uq_product_categories_pair"),
    )
    op.create_index("ix_product_categories_category", "product_categories", ["category_id"])

    # --- 13. coupons ---------------------------------------------------------
    op.create_table(
        "coupons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", _pg_enum("coupon_kind"), nullable=False),
        sa.Column("value", sa.Numeric(12, 2), nullable=False),
        sa.Column("min_order_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("max_discount", sa.Numeric(12, 2), nullable=True),
        sa.Column("usage_limit", sa.Integer(), nullable=True),
        sa.Column("usage_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("per_user_limit", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_coupons"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_coupons_created_by_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_coupons_updated_by_users", ondelete="SET NULL"),
        sa.UniqueConstraint("code", name="uq_coupons_code"),
        sa.CheckConstraint("value >= 0", name="ck_coupons_value_non_negative"),
        sa.CheckConstraint("(kind != 'percent') OR (value <= 100)", name="ck_coupons_percent_max_100"),
        sa.CheckConstraint("usage_count >= 0", name="ck_coupons_usage_count_non_negative"),
        sa.CheckConstraint(
            "(usage_limit IS NULL) OR (usage_count <= usage_limit)",
            name="ck_coupons_usage_within_limit",
        ),
    )
    op.create_index("ix_coupons_active_expires", "coupons", ["is_active", "expires_at"])

    # --- 14. carts -----------------------------------------------------------
    op.create_table(
        "carts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_carts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_carts_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_carts_user_id"),
    )

    # --- 15. cart_items ------------------------------------------------------
    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cart_items"),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"], name="fk_cart_items_cart_id_carts", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], name="fk_cart_items_variant_id_product_variants", ondelete="RESTRICT"),
        sa.UniqueConstraint("cart_id", "variant_id", name="uq_cart_items_cart_variant"),
        sa.CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
    )
    op.create_index("ix_cart_items_cart", "cart_items", ["cart_id"])

    # --- 16. wishlists -------------------------------------------------------
    op.create_table(
        "wishlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_wishlists"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_wishlists_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_wishlists_product_id_products", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "product_id", name="uq_wishlists_user_product"),
    )
    op.create_index("ix_wishlists_user_added", "wishlists", ["user_id", "added_at"])

    # --- 17. orders ----------------------------------------------------------
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("order_number", sa.String(32), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("status", _pg_enum("order_status"), server_default="placed", nullable=False),
        sa.Column("payment_status", _pg_enum("payment_status"), server_default="pending", nullable=False),
        sa.Column("payment_method", _pg_enum("payment_method"), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("shipping_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("tax_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("discount_amount", sa.Numeric(12, 2), server_default="0", nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("coupon_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("shipping_address", postgresql.JSONB(), nullable=False),
        sa.Column("billing_address", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("packed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_orders_user_id_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"], name="fk_orders_coupon_id_coupons", ondelete="SET NULL"),
        sa.UniqueConstraint("order_number", name="uq_orders_order_number"),
        sa.CheckConstraint("subtotal >= 0", name="ck_orders_subtotal_non_negative"),
        sa.CheckConstraint("shipping_amount >= 0", name="ck_orders_shipping_non_negative"),
        sa.CheckConstraint("tax_amount >= 0", name="ck_orders_tax_non_negative"),
        sa.CheckConstraint("discount_amount >= 0", name="ck_orders_discount_non_negative"),
        sa.CheckConstraint("total >= 0", name="ck_orders_total_non_negative"),
    )
    op.create_index("ix_orders_user_created", "orders", ["user_id", "created_at"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_payment_status", "orders", ["payment_status"])
    op.create_index("ix_orders_email", "orders", ["email"])
    op.create_index("ix_orders_created", "orders", ["created_at"])

    # --- 18. order_items -----------------------------------------------------
    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_name", sa.String(255), nullable=False),
        sa.Column("variant_label", sa.String(255), nullable=True),
        sa.Column("sku", sa.String(80), nullable=True),
        sa.Column("primary_image_url", sa.Text(), nullable=True),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Numeric(12, 2), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_order_items_order_id_orders", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_order_items_product_id_products", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], name="fk_order_items_variant_id_product_variants", ondelete="RESTRICT"),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_order_items_unit_price_non_negative"),
        sa.CheckConstraint("line_total >= 0", name="ck_order_items_line_total_non_negative"),
    )
    op.create_index("ix_order_items_order", "order_items", ["order_id"])
    op.create_index("ix_order_items_product", "order_items", ["product_id"])

    # --- 19. payments --------------------------------------------------------
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("razorpay_order_id", sa.String(64), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(64), nullable=True),
        sa.Column("razorpay_signature", sa.String(255), nullable=True),
        sa.Column("webhook_event_id", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="INR", nullable=False),
        sa.Column("status", _pg_enum("razorpay_payment_status"), nullable=False),
        sa.Column("method", sa.String(32), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_payments_order_id_orders", ondelete="CASCADE"),
        sa.UniqueConstraint("razorpay_order_id", name="uq_payments_razorpay_order_id"),
        sa.UniqueConstraint("razorpay_payment_id", name="uq_payments_razorpay_payment_id"),
        sa.UniqueConstraint("webhook_event_id", name="uq_payments_webhook_event_id"),
        sa.CheckConstraint("amount >= 0", name="ck_payments_amount_non_negative"),
    )
    op.create_index("ix_payments_order", "payments", ["order_id"])
    op.create_index("ix_payments_status", "payments", ["status"])

    # --- 20. shipments -------------------------------------------------------
    op.create_table(
        "shipments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("courier_name", sa.String(120), nullable=True),
        sa.Column("tracking_id", sa.String(120), nullable=True),
        sa.Column("tracking_url", sa.Text(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_shipments"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_shipments_order_id_orders", ondelete="CASCADE"),
        sa.UniqueConstraint("order_id", name="uq_shipments_order_id"),
    )
    op.create_index("ix_shipments_tracking_id", "shipments", ["tracking_id"])

    # --- 21. stock_movements -------------------------------------------------
    op.create_table(
        "stock_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("reason", _pg_enum("stock_movement_reason"), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stock_movements"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], name="fk_stock_movements_variant_id_product_variants", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name="fk_stock_movements_order_id_orders", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_stock_movements_actor_user_id_users", ondelete="SET NULL"),
    )
    op.create_index("ix_stock_movements_variant_created", "stock_movements", ["variant_id", "created_at"])
    op.create_index("ix_stock_movements_order", "stock_movements", ["order_id"])

    # --- 22. reviews ---------------------------------------------------------
    op.create_table(
        "reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", _pg_enum("review_status"), server_default="pending", nullable=False),
        sa.Column("moderation_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_reviews_product_id_products", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_reviews_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "product_id", name="uq_reviews_user_product"),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating_range"),
    )
    op.create_index("ix_reviews_deleted_at", "reviews", ["deleted_at"])
    op.create_index("ix_reviews_product_status", "reviews", ["product_id", "status"])

    # --- 23. banners ---------------------------------------------------------
    op.create_table(
        "banners",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("subtitle", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("placement", _pg_enum("banner_placement"), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("mobile_image_url", sa.Text(), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("cta_label", sa.String(80), nullable=True),
        sa.Column("cta_url", sa.Text(), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_banners"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_banners_product_id_products", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="fk_banners_category_id_categories", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_banners_created_by_users", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_banners_updated_by_users", ondelete="SET NULL"),
    )
    op.create_index("ix_banners_deleted_at", "banners", ["deleted_at"])
    op.create_index(
        "ix_banners_placement_active", "banners", ["placement", "is_active"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_banners_window", "banners", ["starts_at", "ends_at"])

    # --- 24. notifications ---------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("channel", _pg_enum("notification_channel"), nullable=False),
        sa.Column("template_key", sa.String(120), nullable=False),
        sa.Column("subject", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", _pg_enum("notification_status"), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_notifications_user_id_users", ondelete="CASCADE"),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"])
    op.create_index(
        "ix_notifications_queued", "notifications", ["scheduled_for"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index("ix_notifications_channel_status", "notifications", ["channel", "status"])

    # --- 25. audit_logs ------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", _pg_enum("audit_action"), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_audit_logs_actor_user_id_users", ondelete="SET NULL"),
    )
    op.create_index("ix_audit_logs_actor_created", "audit_logs", ["actor_user_id", "created_at"])
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_action_created", "audit_logs", ["action", "created_at"])
    op.create_index("ix_audit_logs_request", "audit_logs", ["request_id"])

    # --- Seed system roles ---------------------------------------------------
    bind.execute(sa.text("""
        INSERT INTO roles (name, description, is_system)
        VALUES
          ('customer', 'Default role assigned to every signed-up shopper.', true),
          ('admin',    'Full administrative access to the dashboard.',      true),
          ('staff',    'Limited admin access for support and operations.',  true)
        ON CONFLICT (name) DO NOTHING;
    """))


def downgrade() -> None:
    bind = op.get_bind()

    # Drop search-vector trigger + function before its table.
    bind.execute(sa.text("DROP TRIGGER IF EXISTS products_search_vector_trg ON products;"))
    bind.execute(sa.text("DROP FUNCTION IF EXISTS products_search_vector_update();"))

    # Drop in reverse FK order. Never run this in prod against real data.
    for table in [
        "audit_logs", "notifications", "banners", "reviews",
        "stock_movements", "shipments", "payments", "order_items", "orders",
        "wishlists", "cart_items", "carts", "coupons",
        "product_categories", "inventory", "product_images",
        "product_variants", "products", "categories",
        "addresses", "user_roles", "user_profiles", "users", "roles",
    ]:
        op.drop_table(table)

    for name, _ in reversed(ENUMS):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {name};"))
