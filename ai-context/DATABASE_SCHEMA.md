# DATABASE_SCHEMA.md

> **Status:** PLANNED — no models or migrations exist yet. This is the target schema derived strictly from PRD v1.0.
> When schema is implemented, this doc must be kept in sync with the SQLAlchemy models in `app/models/`.

---

## 1. Conventions

- **Primary keys:** `UUID` (PostgreSQL `uuid` type, default `gen_random_uuid()` via `pgcrypto`). Public-facing identifiers that bots/SEO depend on use **slugs** (see §3 Product).
- **Timestamps:** every table has `created_at TIMESTAMPTZ NOT NULL DEFAULT now()` and `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` (auto-updated via `ON UPDATE` trigger or SQLAlchemy `onupdate=`).
- **Soft delete:** product entities use a `status` enum, not `deleted_at`. See §3 Product.
- **Money:** stored as `NUMERIC(12, 2)` INR. Never `FLOAT`.
- **Enums:** Postgres native enums for stable closed sets (`order_status`, `payment_status`, `product_status`). String columns + CHECK for fast-changing sets.
- **Indexes:** every foreign key gets an index. Hot filter columns (e.g. `product.status`, `order.status`, `order.user_id`) are explicitly indexed below.
- **Naming:** singular table names (`product`, not `products`), snake_case columns.

---

## 2. Entity-Relationship Overview

```
                ┌──────────┐
                │   user   │ (mirrors Clerk user; admins are users with role='admin')
                └────┬─────┘
        ┌───────────┼───────────┬───────────┐
        ▼           ▼           ▼           ▼
  ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌─────────┐
  │ address │ │ wishlist │ │  cart     │ │  order  │
  └─────────┘ └────┬─────┘ └─────┬─────┘ └────┬────┘
                   │             │            │
                   ▼             ▼            ▼
                ┌──────────┐ ┌──────────┐ ┌────────────┐ ┌──────────┐
                │ product  │←┤ cart_item│ │ order_item │→│ payment  │
                └────┬─────┘ └──────────┘ └─────┬──────┘ └──────────┘
                     │                          │
        ┌────────────┼────────────┐        ┌────▼─────┐
        ▼            ▼            ▼        │ shipment │
  ┌──────────┐ ┌────────────┐ ┌─────────┐  └──────────┘
  │ product_ │ │  product_  │ │ product_│
  │  image   │ │  variant   │ │ category│ (m2m → category)
  └──────────┘ └─────┬──────┘ └─────────┘
                     │
                     ▼
              ┌────────────────┐
              │ stock_movement │ (audit log)
              └────────────────┘

         ┌──────────┐
         │ coupon   │ (referenced by order, optional)
         └──────────┘

         ┌──────────────┐
         │ bot_event    │ (lead capture from WhatsApp/Instagram redirects)
         └──────────────┘

         ┌───────────────────┐
         │ request_idempotency│ (idempotency keys for write endpoints)
         └───────────────────┘
```

---

## 3. Tables

### 3.1 `user`

Mirrors the Clerk user; created/updated via Clerk webhook (see [AUTH_FLOW.md](AUTH_FLOW.md)). Guests are **not** stored here — guest checkout uses fields on `order` directly.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `clerk_user_id` | TEXT UNIQUE NOT NULL | From Clerk; never null for registered users |
| `email` | TEXT UNIQUE NOT NULL | |
| `phone` | TEXT NULLABLE | Clerk-provided when phone auth used |
| `full_name` | TEXT NULLABLE | |
| `role` | ENUM(`customer`, `admin`) NOT NULL DEFAULT `customer` | Admin recognised here OR via Clerk metadata — see AUTH_FLOW §3 |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `clerk_user_id`, `email`.

### 3.2 `address`

Saved delivery addresses (PRD §5.3 — SHOULD HAVE). Each user may have many.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → `user.id` ON DELETE CASCADE | |
| `label` | TEXT NULLABLE | "Home", "Office" |
| `recipient_name` | TEXT NOT NULL | |
| `phone` | TEXT NOT NULL | |
| `line1`, `line2` | TEXT | |
| `city`, `state` | TEXT NOT NULL | |
| `postal_code` | TEXT NOT NULL | 6-digit PIN |
| `country` | TEXT NOT NULL DEFAULT `'IN'` | Phase 1 is pan-India only |
| `is_default` | BOOLEAN NOT NULL DEFAULT FALSE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `user_id`; partial unique on `(user_id) WHERE is_default = TRUE`.

### 3.3 `category`

Filter taxonomy. Per PRD §5.1: fabric, occasion, color, price range, region/type. **Exact list is open item #2 in PRD §15** — schema below is generic enough to absorb whatever the client confirms.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `slug` | TEXT UNIQUE NOT NULL | URL-safe, immutable (used in `/sarees/[category]/[slug]` URLs per PRD §10) |
| `name` | TEXT NOT NULL | |
| `kind` | ENUM(`fabric`, `occasion`, `region`, `price_bracket`, `color`) NOT NULL | Drives which filter group it appears in |
| `parent_id` | UUID FK → `category.id` NULLABLE | For hierarchies if needed |
| `display_order` | INTEGER NOT NULL DEFAULT 0 | |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `slug`, `kind`.

### 3.4 `product`

Core catalog table. Per PRD §7: **slug is immutable** after publish.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `slug` | TEXT UNIQUE NOT NULL | Generated at creation; immutable. Drives bot URL. |
| `name` | TEXT NOT NULL | |
| `description` | TEXT | |
| `base_price` | NUMERIC(12,2) NOT NULL | INR. Per-variant price override possible (§3.6). |
| `mrp` | NUMERIC(12,2) NULLABLE | Compare-at price |
| `status` | ENUM(`draft`, `published`, `unavailable`, `archived`) NOT NULL DEFAULT `draft` | `unavailable` = stock=0 (auto); `archived` = soft-deleted (URL still resolves to "Product Unavailable" page per PRD §7.2) |
| `tags` | TEXT[] NOT NULL DEFAULT `'{}'` | Free-form tags for SEO + search |
| `search_vector` | TSVECTOR | Generated column from `name || description || tags` for Postgres FTS |
| `featured` | BOOLEAN NOT NULL DEFAULT FALSE | For homepage "Featured Collections" |
| `created_at`, `updated_at` | TIMESTAMPTZ | |
| `published_at` | TIMESTAMPTZ NULLABLE | First time `status` moved to `published` |

Indexes:
- UNIQUE `slug`
- `status` (B-tree)
- `featured` partial WHERE TRUE
- GIN on `search_vector`
- GIN on `tags`

### 3.5 `product_image`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `product_id` | UUID FK → `product.id` ON DELETE CASCADE | |
| `cloudinary_public_id` | TEXT NOT NULL | Used to construct delivery URLs and to invalidate |
| `url` | TEXT NOT NULL | Cloudinary CDN URL (WebP, sized variants generated on Cloudinary side) |
| `alt_text` | TEXT NULLABLE | Auto-generated from product name + tags per PRD §10 if blank |
| `position` | INTEGER NOT NULL DEFAULT 0 | Gallery order |
| `is_primary` | BOOLEAN NOT NULL DEFAULT FALSE | First card image |
| `created_at` | TIMESTAMPTZ | |

Indexes: `product_id`, partial unique on `(product_id) WHERE is_primary = TRUE`.

### 3.6 `product_variant`

Per PRD §6.1: variants by color and fabric, each with its own stock.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `product_id` | UUID FK → `product.id` ON DELETE CASCADE | |
| `sku` | TEXT UNIQUE NULLABLE | Auto-generated if blank |
| `color` | TEXT NULLABLE | |
| `fabric` | TEXT NULLABLE | |
| `price_override` | NUMERIC(12,2) NULLABLE | If null, falls back to `product.base_price` |
| `stock` | INTEGER NOT NULL DEFAULT 0 CHECK (`stock >= 0`) | |
| `low_stock_threshold` | INTEGER NOT NULL DEFAULT 2 | For dashboard flag |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `product_id`, `sku`.

### 3.7 `product_category` (junction)

| Column | Type | Notes |
|---|---|---|
| `product_id` | UUID FK → `product.id` ON DELETE CASCADE | PK part |
| `category_id` | UUID FK → `category.id` ON DELETE CASCADE | PK part |

Composite PK `(product_id, category_id)`. Indexes on both columns.

### 3.8 `cart` & `cart_item`

Server-side cart for registered users (mergeable on login). Guest carts live client-side in localStorage and are submitted at checkout — no server row for guests.

**`cart`:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → `user.id` ON DELETE CASCADE UNIQUE | One cart per user |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

**`cart_item`:**
| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `cart_id` | UUID FK → `cart.id` ON DELETE CASCADE | |
| `product_id` | UUID FK → `product.id` ON DELETE RESTRICT | |
| `variant_id` | UUID FK → `product_variant.id` ON DELETE RESTRICT | |
| `quantity` | INTEGER NOT NULL CHECK (`quantity > 0`) | |
| `added_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `cart_id`, UNIQUE `(cart_id, variant_id)`.

### 3.9 `order`

Supports both guest and registered checkout (per PRD §5.2).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `order_number` | TEXT UNIQUE NOT NULL | Human-readable, e.g. `OOR-202605-00001` |
| `user_id` | UUID FK → `user.id` NULLABLE | NULL for guest orders |
| `email` | TEXT NOT NULL | Snapshot at order time |
| `phone` | TEXT NOT NULL | |
| `customer_name` | TEXT NOT NULL | |
| `status` | ENUM(`placed`, `packed`, `shipped`, `delivered`, `cancelled`) NOT NULL DEFAULT `placed` | Drives customer tracking UI per PRD §5.2 |
| `payment_status` | ENUM(`pending`, `paid`, `failed`, `refunded`, `cod_pending`) NOT NULL DEFAULT `pending` | |
| `payment_method` | ENUM(`razorpay`, `cod`) NOT NULL | |
| `subtotal` | NUMERIC(12,2) NOT NULL | |
| `shipping_amount` | NUMERIC(12,2) NOT NULL DEFAULT 0 | |
| `tax_amount` | NUMERIC(12,2) NOT NULL DEFAULT 0 | |
| `discount_amount` | NUMERIC(12,2) NOT NULL DEFAULT 0 | |
| `total` | NUMERIC(12,2) NOT NULL | `subtotal + shipping + tax - discount` |
| `coupon_id` | UUID FK → `coupon.id` NULLABLE | |
| `shipping_address` | JSONB NOT NULL | Frozen snapshot of address at checkout |
| `notes` | TEXT NULLABLE | Customer note |
| `placed_at`, `packed_at`, `shipped_at`, `delivered_at`, `cancelled_at` | TIMESTAMPTZ NULLABLE | Audit trail of status changes |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `order_number`, `user_id`, `status`, `payment_status`, `created_at DESC`.

### 3.10 `order_item`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `order_id` | UUID FK → `order.id` ON DELETE CASCADE | |
| `product_id` | UUID FK → `product.id` ON DELETE RESTRICT | |
| `variant_id` | UUID FK → `product_variant.id` ON DELETE RESTRICT | |
| `product_name` | TEXT NOT NULL | Snapshot — name may change later |
| `variant_label` | TEXT | Snapshot — e.g. "Maroon · Kanchipuram Silk" |
| `unit_price` | NUMERIC(12,2) NOT NULL | Snapshot at purchase |
| `quantity` | INTEGER NOT NULL CHECK (`quantity > 0`) | |
| `line_total` | NUMERIC(12,2) NOT NULL | `unit_price * quantity` |

Indexes: `order_id`, `product_id`.

### 3.11 `payment`

Razorpay record. One order may have multiple payment rows (failed → retried).

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `order_id` | UUID FK → `order.id` ON DELETE RESTRICT | |
| `razorpay_order_id` | TEXT NOT NULL | From Razorpay `orders.create` |
| `razorpay_payment_id` | TEXT NULLABLE | Set after success |
| `razorpay_signature` | TEXT NULLABLE | Verified, then stored for audit |
| `amount` | NUMERIC(12,2) NOT NULL | Paise → INR converted on store |
| `currency` | TEXT NOT NULL DEFAULT `'INR'` | |
| `status` | ENUM(`created`, `authorized`, `captured`, `failed`, `refunded`) NOT NULL | Mirrors Razorpay |
| `method` | TEXT NULLABLE | upi / card / netbanking / wallet (from Razorpay payload) |
| `failure_reason` | TEXT NULLABLE | |
| `webhook_event_id` | TEXT NULLABLE | For idempotency — Razorpay event id |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Indexes: `order_id`, UNIQUE `razorpay_order_id`, UNIQUE `razorpay_payment_id`, UNIQUE `webhook_event_id`.

### 3.12 `shipment`

Per PRD §11: manual courier assignment at launch.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `order_id` | UUID FK → `order.id` ON DELETE CASCADE UNIQUE | One shipment per order in Phase 1 |
| `courier_name` | TEXT NULLABLE | Free-text at launch (Shiprocket integration is Phase 2) |
| `tracking_id` | TEXT NULLABLE | Admin-entered |
| `tracking_url` | TEXT NULLABLE | Optional courier URL |
| `shipped_at`, `delivered_at` | TIMESTAMPTZ NULLABLE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### 3.13 `stock_movement` (audit log)

Every change to `product_variant.stock` writes one row here.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `variant_id` | UUID FK → `product_variant.id` | |
| `delta` | INTEGER NOT NULL | Negative on order, positive on restock |
| `reason` | ENUM(`order_placed`, `order_cancelled`, `manual_adjustment`, `csv_import`, `restock`) NOT NULL | |
| `order_id` | UUID FK → `order.id` NULLABLE | |
| `actor_user_id` | UUID FK → `user.id` NULLABLE | NULL for system-triggered |
| `note` | TEXT NULLABLE | |
| `created_at` | TIMESTAMPTZ | |

Indexes: `variant_id, created_at DESC`, `order_id`.

### 3.14 `coupon`

PRD §5.2 marks Discount Codes as SHOULD HAVE.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `code` | TEXT UNIQUE NOT NULL | Case-insensitive (use CITEXT or store uppercase) |
| `kind` | ENUM(`percent`, `flat`) NOT NULL | |
| `value` | NUMERIC(12,2) NOT NULL | Percent (0–100) or INR amount |
| `min_order_amount` | NUMERIC(12,2) NOT NULL DEFAULT 0 | |
| `max_discount` | NUMERIC(12,2) NULLABLE | Cap for percent coupons |
| `usage_limit` | INTEGER NULLABLE | Total uses; NULL = unlimited |
| `usage_count` | INTEGER NOT NULL DEFAULT 0 | |
| `per_user_limit` | INTEGER NULLABLE | |
| `starts_at`, `expires_at` | TIMESTAMPTZ NULLABLE | |
| `is_active` | BOOLEAN NOT NULL DEFAULT TRUE | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### 3.15 `wishlist`

| Column | Type | Notes |
|---|---|---|
| `user_id` | UUID FK → `user.id` ON DELETE CASCADE | PK part |
| `product_id` | UUID FK → `product.id` ON DELETE CASCADE | PK part |
| `added_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Composite PK `(user_id, product_id)`. Indexes on both.

### 3.16 `recently_viewed`

Per PRD §5.3 — SHOULD HAVE, last 10 viewed per session/user.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `user_id` | UUID FK → `user.id` ON DELETE CASCADE NULLABLE | NULL for guests |
| `session_id` | TEXT NULLABLE | For guests (cookie-bound) |
| `product_id` | UUID FK → `product.id` ON DELETE CASCADE | |
| `viewed_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Indexes: `(user_id, viewed_at DESC)`, `(session_id, viewed_at DESC)`. Pruning is read-time (`LIMIT 10`); a periodic cleanup job removes rows older than 30 days.

### 3.17 `bot_event` (optional, supports PRD §6.5 Traffic Source — NICE TO HAVE)

If the WhatsApp/Instagram bots ping the backend on each redirect (TBD with Dev 3, see [WHATSAPP_AUTOMATION.md](WHATSAPP_AUTOMATION.md)), we record one row per redirect for attribution.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `source` | ENUM(`whatsapp`, `instagram`) NOT NULL | |
| `product_id` | UUID FK → `product.id` NULLABLE | Null if invalid slug |
| `slug` | TEXT NOT NULL | Raw slug sent by the bot |
| `external_user_ref` | TEXT NULLABLE | Hashed bot-side identifier — never the raw phone number |
| `event_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| `metadata` | JSONB | |

Indexes: `(product_id, event_at DESC)`, `source`.

**Status:** UNCONFIRMED. Build only if the bot side actually emits events. Otherwise drop from schema.

### 3.18 `request_idempotency`

Backing store for `Idempotency-Key` header on write endpoints (notably `POST /checkout/orders` and `POST /payments/verify`).

| Column | Type | Notes |
|---|---|---|
| `key` | TEXT PK | Client-supplied; scoped per route |
| `route` | TEXT NOT NULL | |
| `user_id` | UUID FK → `user.id` NULLABLE | |
| `response_status` | INTEGER NOT NULL | |
| `response_body` | JSONB NOT NULL | |
| `created_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | |

TTL: scheduled DELETE for rows older than 24h.

---

## 4. Postgres Extensions Required

- `pgcrypto` — `gen_random_uuid()`
- `citext` — case-insensitive `coupon.code` (optional; uppercase-on-insert also works)
- (Future) `pg_trgm` — fuzzy search if FTS proves insufficient

Loaded via the first Alembic migration.

---

## 5. Full-Text Search

- `product.search_vector` is a generated column:
  ```sql
  search_vector tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(description, '')), 'B') ||
    setweight(to_tsvector('english', coalesce(array_to_string(tags, ' '), '')), 'C')
  ) STORED;
  ```
- GIN index on `search_vector`.
- Queries use `plainto_tsquery('english', :q)` for forgiving user input.
- If precision/recall becomes a problem post-launch, migrate to Algolia (per PRD §8.1).

---

## 6. Migration Discipline

- One Alembic revision per logical change. Never edit a migration after it has been run in any deployed environment.
- Migrations are reviewed for backward compatibility (no destructive DROPs without a deprecation cycle once live data exists).
- Every model change comes with both `upgrade()` and `downgrade()` paths.
- Seed data lives in `scripts/seed_dev.py`, never in migrations.

---

## 7. Open Schema Questions

| Question | Default if unanswered |
|---|---|
| Tag storage — `TEXT[]` column vs separate `tag` table? | `TEXT[]` for Phase 1 (simpler, indexable via GIN); revisit if admin needs tag analytics |
| Should guest orders also write to a `guest` table for marketing reuse? | No — `order.email/phone` is sufficient until a CRM is added |
| Multi-currency? | No. INR only. Schema does carry `payment.currency` for future-proofing only. |
| Multi-warehouse stock? | No. Single virtual warehouse; `product_variant.stock` is total available. |
| Cart expiry / abandoned cart cleanup? | No automatic deletion for Phase 1; can add a 60-day TTL job later. |
