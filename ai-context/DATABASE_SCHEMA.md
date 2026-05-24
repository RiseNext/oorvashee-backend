# DATABASE_SCHEMA.md

> **Status:** IMPLEMENTED — 24 tables, 14 native ENUMs, baseline + initial migrations applied.
> Source of truth: the SQLAlchemy models in [`app/models/`](../app/models/). This document is a navigable index + the cross-cutting design decisions; do not duplicate column-by-column listings — read the model file.

---

## 1. Conventions

| Concern | Convention | Where enforced |
|---|---|---|
| Table names | Plural snake_case (`products`, `order_items`) | each model's `__tablename__` |
| Model names | Singular PascalCase (`Product`, `OrderItem`) | class names |
| Primary keys | UUID v4, `gen_random_uuid()` server default | `UUIDPrimaryKeyMixin` in [base.py](../app/db/base.py) |
| Timestamps | `created_at`, `updated_at` TIMESTAMPTZ | `TimestampMixin` in [base.py](../app/db/base.py) |
| Soft delete | Nullable `deleted_at`; repos filter `IS NULL` | `SoftDeleteMixin` in [base.py](../app/db/base.py) |
| Admin auditability | `created_by`, `updated_by` FK to `users.id` | `AuditableMixin` in [base.py](../app/db/base.py) |
| Money | `Numeric(12, 2)` INR; never float | every monetary column |
| Enums | Postgres native `ENUM` types (14 total) | [`models/enums.py`](../app/models/enums.py) + migration `0002` |
| Constraint naming | `ix_*` / `uq_*` / `ck_*` / `fk_*` / `pk_*` | `NAMING_CONVENTION` in [base.py](../app/db/base.py) |
| Indexing | Every FK indexed; hot filter columns indexed explicitly | per-model `__table_args__` |
| Stock locking | `SELECT ... FOR UPDATE` on `inventory` row before decrement | Cycle 2 service layer |

Extensions enabled by migrations:
- `pgcrypto` — `gen_random_uuid()` (migration `0001`)

---

## 2. Table Inventory (24 tables)

| # | Table | Model file | Purpose |
|---|---|---|---|
| 1 | `roles` | [role.py](../app/models/role.py) | RBAC role names. Seeded: customer / admin / staff. |
| 2 | `users` | [user.py](../app/models/user.py) | Identity; Clerk-linked. Soft delete. |
| 3 | `user_profiles` | [user_profile.py](../app/models/user_profile.py) | Display/personal info; 1:1 with users. |
| 4 | `user_roles` | [role.py](../app/models/role.py) | Many-to-many users ↔ roles. |
| 5 | `addresses` | [address.py](../app/models/address.py) | Saved customer shipping addresses. Soft delete. |
| 6 | `categories` | [category.py](../app/models/category.py) | Taxonomy by kind (fabric/occasion/region/price-bracket/color/collection). Self-referencing for hierarchy. |
| 7 | `product_categories` | [category.py](../app/models/category.py) | Products ↔ categories junction. |
| 8 | `products` | [product.py](../app/models/product.py) | Catalog. **Immutable slug.** Trigger-maintained TSVECTOR for FTS (see §5). |
| 9 | `product_variants` | [product_variant.py](../app/models/product_variant.py) | SKU-level. Color/fabric/size + JSONB attributes. |
| 10 | `product_images` | [product_image.py](../app/models/product_image.py) | Cloudinary-hosted; one primary per product. |
| 11 | `inventory` | [inventory.py](../app/models/inventory.py) | Stock + reserved per variant. CHECK constraints prevent negative / over-reserve. |
| 12 | `stock_movements` | [inventory.py](../app/models/inventory.py) | Append-only audit of every stock change. |
| 13 | `coupons` | [coupon.py](../app/models/coupon.py) | Discount codes (percent/flat). CHECK keeps percent ≤ 100. |
| 14 | `carts` | [cart.py](../app/models/cart.py) | One per registered user. Guests stay client-side. |
| 15 | `cart_items` | [cart.py](../app/models/cart.py) | Variant + quantity; UNIQUE(cart, variant). |
| 16 | `wishlists` | [wishlist.py](../app/models/wishlist.py) | User × product association. |
| 17 | `orders` | [order.py](../app/models/order.py) | Guest or authenticated. Frozen address JSONB. Status timestamps. |
| 18 | `order_items` | [order.py](../app/models/order.py) | Snapshotted product/variant/price. |
| 19 | `payments` | [payment.py](../app/models/payment.py) | Razorpay records; UNIQUE on webhook_event_id for idempotency. |
| 20 | `shipments` | [payment.py](../app/models/payment.py) | One per order; courier + tracking. |
| 21 | `reviews` | [review.py](../app/models/review.py) | Product reviews with moderation workflow. |
| 22 | `banners` | [banner.py](../app/models/banner.py) | Placement-aware promo banners. |
| 23 | `notifications` | [notification.py](../app/models/notification.py) | Email / SMS / WhatsApp / in-app outbox. |
| 24 | `audit_logs` | [audit_log.py](../app/models/audit_log.py) | Append-only system audit (admin actions, payments, status changes). |

---

## 3. Postgres ENUM Types

Defined by name in migration `0002_initial_schema.py` and referenced via `create_type=False` from every model, so the migration owns the lifecycle and the models never accidentally recreate them.

| ENUM type | Values |
|---|---|
| `user_status` | active, disabled |
| `category_kind` | fabric, occasion, region, price_bracket, color, collection |
| `product_status` | draft, published, unavailable, archived |
| `order_status` | placed, packed, shipped, delivered, cancelled |
| `payment_status` | pending, paid, failed, refunded, cod_pending |
| `payment_method` | razorpay, cod |
| `razorpay_payment_status` | created, authorized, captured, failed, refunded |
| `coupon_kind` | percent, flat |
| `stock_movement_reason` | order_placed, order_cancelled, manual_adjustment, csv_import, restock |
| `review_status` | pending, approved, rejected |
| `banner_placement` | homepage_hero, homepage_secondary, category_top, cart_promo |
| `notification_channel` | email, sms, whatsapp, in_app |
| `notification_status` | queued, sent, failed, read |
| `audit_action` | create, update, delete, login, logout, payment_captured, stock_adjusted, status_changed |

Adding a value to an existing enum requires `ALTER TYPE ... ADD VALUE` in a new migration — keep enums tight to avoid that ceremony.

---

## 4. Key Indexes

These are the load-bearing ones — read [`app/models/`](../app/models/) for the full list per table.

| Index | Purpose |
|---|---|
| `ix_products_search_vector` (GIN) | Catalog full-text search via `plainto_tsquery` |
| `ix_products_tags` (GIN) | Tag filtering |
| `ix_products_status` | Hide non-published products |
| `ix_products_featured` (partial: `featured = true AND status = 'published'`) | Homepage featured row |
| `ix_orders_user_created` | Account order history |
| `ix_orders_status`, `ix_orders_payment_status` | Admin queues |
| `ix_inventory_low_stock` | Admin low-stock dashboard |
| `ix_stock_movements_variant_created` | Audit lookup per variant |
| `ix_audit_logs_actor_created`, `ix_audit_logs_entity`, `ix_audit_logs_action_created` | Audit pivots |
| `uq_addresses_user_default` (partial: `is_default = true AND deleted_at IS NULL`) | At most one default address per user |
| `uq_product_variants_default_per_product` (partial) | At most one default variant per product |
| `uq_product_images_primary_per_product` (partial) | At most one primary image per product |
| `ix_notifications_queued` (partial: `status = 'queued'`) | Worker poll target |

---

## 5. Full-Text Search

`products.search_vector` is a plain `TSVECTOR` column maintained by a Postgres trigger created in migration `0002`:

```sql
CREATE FUNCTION products_search_vector_update() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_vector :=
    setweight(to_tsvector('english'::regconfig, coalesce(NEW.name, '')),              'A') ||
    setweight(to_tsvector('english'::regconfig, coalesce(NEW.short_description, '')), 'B') ||
    setweight(to_tsvector('english'::regconfig, coalesce(NEW.description, '')),       'C') ||
    setweight(to_tsvector('english'::regconfig, coalesce(array_to_string(NEW.tags, ' '), '')), 'C');
  RETURN NEW;
END $$;

CREATE TRIGGER products_search_vector_trg
BEFORE INSERT OR UPDATE OF name, short_description, description, tags
ON products
FOR EACH ROW EXECUTE FUNCTION products_search_vector_update();
```

**Why a trigger and not a generated column.** Postgres requires generated-column expressions to use only `IMMUTABLE` functions. `to_tsvector('english', text)` is `STABLE`, not `IMMUTABLE`, because the `'english'` argument resolves via an implicit `text → regconfig` cast at runtime — and that cast is itself `STABLE`. Postgres therefore refuses the `GENERATED ALWAYS AS (...)` form with `generation expression is not immutable`.

**Application contract:** never write to `search_vector` from app code. The trigger overwrites it on every INSERT and on any UPDATE that touches `name`, `short_description`, `description`, or `tags`. Updating an unrelated column (e.g. `status`) does NOT re-run the trigger — that's intentional, avoids needless writes.

Indexed via GIN (`ix_products_search_vector`). Queries use `plainto_tsquery('english', :q)` (forgiving of user typos / partial input). Migration to Algolia is mentioned in PRD §8.1 but not needed until precision/recall justifies it.

---

## 6. Migration Discipline

- **One revision per logical change.** Never edit a migration that's been run anywhere.
- **Hand-written for the schema baseline** (`0002_initial_schema.py`) — clearer for review than autogenerate output, ordered for FK safety, idempotent on ENUM creation.
- **Autogenerate for future evolution** — when models change, `uv run alembic revision --autogenerate -m "..."` produces a draft. Always review before committing.
- **Both `upgrade()` and `downgrade()`** implemented on every revision.
- Now that real data exists in Neon, every migration must be **expand-then-contract** — safely deployable while the previous app version is still running.

---

## 6a. Seed System

[`app/seeds/`](../app/seeds/) provides dev/staging seed data:

- **`data.py`** — declarative tables of categories, products, banners. Editable by anyone; loaders resolve cross-references (product → category slugs → IDs) at apply time.
- **`runner.py`** — `run_seeds` / `reset_seeds` / `seed_status` async functions. Idempotent on natural keys (slug, SKU, title+placement). Mutable fields (price, description, stock) are refreshed on re-run via `update_fields`.
- **`base.py`** — `get_or_create` helper + `SeedReport` aggregator + placeholder image URL helper.

CLI: `python -m scripts.seed_dev {run | reset --yes | reseed --yes | status}`. Refuses to execute when `APP_ENV=prod`. Use a separate `bootstrap_prod.py` for production-only bootstrap data (admin user, T&Cs, system categories) — never repurpose this script.

**Why the seeds don't write to `audit_logs`:** the seed system bypasses service-layer auditing on purpose. Audit rows describe human/admin actions; seed-applied rows describe a machine-managed dev environment. If you need an audit trail for production bootstrap, write it explicitly in the bootstrap script.

---

## 7. Open Schema Questions

| Question | Default |
|---|---|
| Multi-warehouse stock | Single virtual warehouse for Phase 1. `inventory` table is keyed by variant; add `warehouse_id` later without touching read paths. |
| Reserved stock window | Column exists (`inventory.reserved`); decrement-on-checkout policy lands with cart/checkout service in Cycle 2. |
| Bundle products | Not in Phase 1. Each SKU stands alone. |
| Recently-viewed | Not modelled at the DB level — frontend localStorage suffices until cross-device need appears. |
| Bot event capture | Tabled — `bot_events` table will land only if Dev 3 confirms Option B (see [WHATSAPP_AUTOMATION.md](WHATSAPP_AUTOMATION.md)). |
