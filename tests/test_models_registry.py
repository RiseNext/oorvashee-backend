"""Smoke test: every expected table is registered on Base.metadata.

Catches a missing import in `app/models/__init__.py` before it silently makes
Alembic miss a table on autogenerate.
"""

from __future__ import annotations

EXPECTED_TABLES = {
    "addresses",
    "audit_logs",
    "banners",
    "cart_items",
    "carts",
    "categories",
    "checkout_sessions",
    "coupons",
    "import_jobs",
    "inventory",
    "notifications",
    "order_items",
    "orders",
    "payments",
    "policies",
    "product_categories",
    "product_images",
    "product_variants",
    "products",
    "request_idempotency",
    "reservations",
    "reviews",
    "roles",
    "shipments",
    "stock_movements",
    "user_profiles",
    "user_roles",
    "users",
    "wishlists",
}


def test_all_models_registered_on_metadata() -> None:
    from app.models import Base

    actual = set(Base.metadata.tables.keys())
    missing = EXPECTED_TABLES - actual
    extra = actual - EXPECTED_TABLES
    assert not missing, f"Missing tables (not imported in app/models/__init__.py): {missing}"
    assert not extra, f"Unexpected tables (update EXPECTED_TABLES): {extra}"


def test_every_table_has_primary_key() -> None:
    from app.models import Base

    for name, table in Base.metadata.tables.items():
        assert table.primary_key.columns, f"{name} has no primary key"
