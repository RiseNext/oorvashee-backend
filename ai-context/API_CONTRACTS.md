# API_CONTRACTS.md

> **Status:** MIXED — health + catalog reads are implemented; the rest is the target shape.
> Live source of truth for shipped endpoints: the FastAPI OpenAPI doc at `/openapi.json` (or `/docs` in non-prod).
> Implemented endpoints are marked ✅ in the per-section tables below.

## 0. Implemented (live)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/v1/health/live` | none | Process liveness (no DB hit) |
| GET | `/api/v1/health` | none | Readiness + DB ping; returns `{ "status": "ok", "db": "connected" }` |
| GET | `/api/v1/products` | none | List with `q, category, min_price, max_price, sort, page, page_size` |
| GET | `/api/v1/products/{slug}` | none | Detail; returns 200 with `available=false` for archived slugs (bot URL contract) |
| GET | `/api/v1/categories` | none | Active categories grouped by `kind` |
| POST | `/api/v1/checkout/quote` | optional | Server-recompute totals + availability for the given lines |
| POST | `/api/v1/checkout/orders` | optional | Place order. **Requires `Idempotency-Key`.** Returns Razorpay handoff for online; `cod_pending` for COD |
| POST | `/api/v1/payments/verify` | optional | Frontend-initiated Razorpay verify + capture. **Requires `Idempotency-Key`.** Replay-safe |
| GET | `/api/v1/orders/{order_number}?email=` | none | Anonymous order tracking; 404 on email mismatch (no info leak) |
| GET | `/api/v1/account/cart` | Bearer | Authenticated cart |
| POST | `/api/v1/account/cart/items` | Bearer | Add (or increment) a variant |
| PATCH | `/api/v1/account/cart/items/{id}` | Bearer | Set quantity |
| DELETE | `/api/v1/account/cart/items/{id}` | Bearer | Remove a line |
| DELETE | `/api/v1/account/cart` | Bearer | Clear cart |
| POST | `/api/v1/account/cart/merge` | Bearer | Merge guest-localStorage cart after sign-in |
| GET | `/api/v1/account/wishlist` | Bearer | List wishlisted products |
| PUT | `/api/v1/account/wishlist/{product_id}` | Bearer | Add (idempotent) |
| DELETE | `/api/v1/account/wishlist/{product_id}` | Bearer | Remove (idempotent) |
| POST | `/api/v1/account/wishlist/{product_id}/move-to-cart` | Bearer | Add variant to cart; optionally remove from wishlist |
| GET | `/api/v1/account/orders` | Bearer | Order history (paginated) |
| GET | `/api/v1/account/orders/{order_number}` | Bearer | Order detail (own orders only) |
| GET    | `/api/v1/admin/products` | admin | Filters: q, status[], category_slug, featured_only. Paginated. |
| POST   | `/api/v1/admin/products` | admin | Create — slug auto-derived from name. Status defaults DRAFT. |
| GET    | `/api/v1/admin/products/{id}` | admin | Full admin view with variants + inventory + images + category links. |
| PATCH  | `/api/v1/admin/products/{id}` | admin | Partial update. `slug` + `status` silently ignored if sent. |
| POST   | `/api/v1/admin/products/{id}/publish` | admin | DRAFT/UNAVAILABLE → PUBLISHED. Requires ≥1 active variant. |
| POST   | `/api/v1/admin/products/{id}/unpublish` | admin | PUBLISHED → DRAFT. Preserves `published_at`. |
| POST   | `/api/v1/admin/products/{id}/archive` | admin | Any → ARCHIVED. Slug stays reserved per PRD §7.2. |
| POST   | `/api/v1/admin/products/{id}/unarchive` | admin | ARCHIVED → DRAFT. Forces deliberate two-step before re-publish. |
| PUT    | `/api/v1/admin/products/{id}/categories` | admin | Replace category set (idempotent). |
| POST   | `/api/v1/admin/products/{id}/variants` | admin | Add variant + seed inventory row. |
| PATCH  | `/api/v1/admin/products/{id}/variants/{vid}` | admin | Partial variant update. |
| DELETE | `/api/v1/admin/products/{id}/variants/{vid}` | admin | Soft-deactivate (`is_active=false`). |
| GET    | `/api/v1/admin/categories` | admin | Filters: q, kind[], is_active, parent_id. Paginated. Each row carries total + published product counts (one round-trip aggregation). |
| POST   | `/api/v1/admin/categories` | admin | Create. Slug auto-derived; `kind` required + immutable thereafter. |
| GET    | `/api/v1/admin/categories/{id}` | admin | Full admin view. |
| PATCH  | `/api/v1/admin/categories/{id}` | admin | Partial update. `slug` + `kind` + `is_active` silently ignored if sent. Parent change enforces self-cycle / descendant-cycle / depth-cap (MAX_DEPTH=3) checks. Image swaps trigger best-effort Cloudinary destroy of the old `public_id`. |
| POST   | `/api/v1/admin/categories/{id}/archive` | admin | `is_active=false`. Hides from catalog filter UI. |
| POST   | `/api/v1/admin/categories/{id}/unarchive` | admin | `is_active=true`. |
| PUT    | `/api/v1/admin/categories/{id}/order` | admin | Set `display_order` (single-row reorder). |
| GET    | `/api/v1/admin/inventory` | admin | Filters: q, low_stock_only, out_of_stock_only. Paginated. JOIN-eager (no N+1). Items carry stock + reserved + available + threshold + is_low + is_out + price. |
| GET    | `/api/v1/admin/inventory/health` | admin | One-glance summary: active variants, total + reserved + available units, out/low/healthy counts. Single round-trip aggregate. |
| GET    | `/api/v1/admin/inventory/movements` | admin | Global stock-movement audit timeline. Filters: variant_id, reason[], actor_user_id, order_id, since, until. Newest first. |
| GET    | `/api/v1/admin/inventory/{variant_id}` | admin | Variant detail + last 20 movements. |
| GET    | `/api/v1/admin/inventory/{variant_id}/movements` | admin | Per-variant audit timeline. |
| POST   | `/api/v1/admin/inventory/{variant_id}/adjust` | admin | Manual stock adjustment. Modes: increment / decrement / set. Reason whitelist: `manual_adjustment`, `restock`. Row-locked. Refuses below 0 or below reserved. Auto-flips parent product PUBLISHED ↔ UNAVAILABLE. Writes `stock_movements` + `audit_logs`. |
| PATCH  | `/api/v1/admin/inventory/{variant_id}/threshold` | admin | Update `low_stock_threshold` (metadata only — no row lock). |
| GET    | `/api/v1/admin/orders` | admin | Filters: q, status[], payment_status[], payment_method, since, until, has_shipment, sort. Paginated. Per-row aggregates (item_count, has_shipment) computed in one extra round-trip. |
| GET    | `/api/v1/admin/orders/summary` | admin | Dashboard tile — status bucket counts + payment-state breakdown + total_revenue_paid. Single aggregate query. |
| GET    | `/api/v1/admin/orders/{id}` | admin | Full order + items + payments + shipment + timeline (synthesised from timestamps + payments + audit_logs). |
| POST   | `/api/v1/admin/orders/{id}/packed` | admin | placed → packed. Razorpay orders require `payment_status=paid`; COD requires `cod_pending` or `paid`. Idempotent. |
| POST   | `/api/v1/admin/orders/{id}/shipped` | admin | packed → shipped. Body requires `courier_name` + `tracking_id`. Creates/updates Shipment row + stamps `shipped_at`. Idempotent. |
| POST   | `/api/v1/admin/orders/{id}/delivered` | admin | shipped → delivered. COD orders auto-flip `payment_status` → `paid`. Stamps `shipment.delivered_at`. Idempotent. |
| POST   | `/api/v1/admin/orders/{id}/cancel` | admin | Non-delivered status → cancelled. Body requires `reason` (min 3 chars). Restocks all line items + recomputes parent product availability. Writes `requires_refund=true` audit metadata when payment_status was PAID. Refuses on delivered orders (returns flow is Phase 4). Idempotent. |
| POST   | `/api/v1/admin/orders/{id}/cod-paid` | admin | COD-only: payment_status cod_pending → paid. For when admin records cash receipt before marking delivered. Idempotent. |
| PUT    | `/api/v1/admin/orders/{id}/shipment` | admin | Update courier/tracking/url on an existing shipment. Doesn't change `shipped_at`. |
| POST   | `/api/v1/admin/orders/{id}/notes` | admin | Add an admin note. Stored on audit_logs with `metadata.type=admin_note`. Separate from `order.notes` (customer-facing). |
| GET    | `/api/v1/admin/analytics/overview` | admin | Top-line KPIs (revenue, orders, AOV, units, new customers). Defaults to last 30 days; 366-day cap. `Cache-Control: max-age=60`. |
| GET    | `/api/v1/admin/analytics/trend` | admin | Time series (revenue + orders + units) bucketed by `day` / `week` / `month`. Dense buckets — zeros filled for quiet days. |
| GET    | `/api/v1/admin/analytics/top-products` | admin | Top-N (default 10, max 100) by `revenue` or `units` over range. |
| GET    | `/api/v1/admin/analytics/top-categories` | admin | Top-N categories over range. Multi-tag products contribute to every category they belong to. |
| GET    | `/api/v1/admin/analytics/fulfillment` | admin | "Right now" operational tiles — awaiting_packing, awaiting_shipment, in_transit, delivered_today (UTC), cod_outstanding, shipped_missing_tracking. No time-range param. |
| GET    | `/api/v1/admin/analytics/customers` | admin | new / repeat / returning / guest_orders / total_active_customers for the window. |
| GET    | `/api/v1/admin/customers` | admin | Paginated customer list with order aggregates baked in (orders_count, paid_orders_count, lifetime_value, last_order_at). Filters: q (email/phone/full_name), has_orders, include_deleted. Sort: newest / oldest / ltv_desc / orders_desc / last_order_desc. |
| GET    | `/api/v1/admin/customers/summary` | admin | Segment counts: total / new_30d / with_orders / with_paid_orders / repeat (≥2 paid) / dormant (no paid order in 90 days) / deleted. Single aggregate query. |
| GET    | `/api/v1/admin/customers/{user_id}` | admin | Full detail: profile + addresses + roles + KPI tile (LTV, AOV, days_since_last_order, is_repeat, account_age) + recent 5 orders. |
| GET    | `/api/v1/admin/customers/{user_id}/orders` | admin | Paginated full order history for one customer. |
| GET    | `/api/v1/admin/customers/{user_id}/activity` | admin | Timeline merged from 4 sources: sign-up + orders + role assignments + admin notes. Newest first. |
| POST   | `/api/v1/admin/customers/{user_id}/notes` | admin | Add an admin note. Stored on audit_logs with `metadata.type=admin_note`. Refuses on deleted customers. |
| GET    | `/api/v1/admin/imports` | admin | Paginated list of import jobs. Filters: kind, status. |
| GET    | `/api/v1/admin/imports/{job_id}` | admin | Job detail — status, progress, row-level errors (capped at 1000 entries), summary. |
| POST   | `/api/v1/admin/imports/products` | admin | Multipart CSV upload. Returns 202 with job_id; processing happens via BackgroundTasks. `?dry_run=true` validates without persisting. 50 MB hard cap. |
| POST   | `/api/v1/admin/imports/variants` | admin | Same shape. CSV references parent products by `product_slug`. Creates variant + initial inventory row atomically. |
| POST   | `/api/v1/admin/imports/inventory` | admin | Same shape. Adjusts stock via the same row-lock + recompute path as 3C. Use `mode=set` for retry-safe re-imports. |
| GET    | `/api/v1/admin/imports/templates/{kind}` | admin | Download starter CSV template (kind ∈ `product`, `variant`, `inventory`). |
| POST | `/api/v1/webhooks/razorpay` | signature | HMAC-verified; idempotent via `payments.webhook_event_id` UNIQUE |
| POST | `/api/v1/webhooks/clerk` | signature | Svix-verified; handles `user.created` / `user.updated` / `user.deleted`. Other event types 200-ignored. |
| POST | `/api/v1/admin/media/sign` | Bearer + admin | Returns signed Cloudinary upload envelope (folder, max_file_size, allowed_formats, eager all baked into signature). Context: `product` / `category` / `banner` / `reel` / `influencer` / `whatsapp_catalog`. |
| POST | `/api/v1/admin/products/{id}/images` | Bearer + admin | Attaches Cloudinary-uploaded image after verifying response signature + folder ownership. First image auto-becomes primary. |
| DELETE | `/api/v1/admin/products/{id}/images/{image_id}` | Bearer + admin | Removes DB row, destroys Cloudinary asset (best-effort), promotes next image to primary if needed. |

Everything below is planned shape.

---

## 1. Conventions

- **Base path:** `/api/v1`. Versioned from day one so breaking changes ship under `/api/v2` without touching live clients.
- **Auth:** `Authorization: Bearer <Clerk JWT>` on all `/account/*` and `/admin/*` routes. Public catalog routes are unauthenticated.
- **Content type:** `application/json` for both request and response (except Cloudinary direct uploads, which bypass the backend).
- **Pagination:** cursor or offset, route-specific. Default `page_size=20`, max `100`. Responses include `{ "items": [...], "next_cursor": str | null, "total": int | null }`.
- **Errors:** uniform shape — `{ "detail": str, "code": str, "request_id": str }`. HTTP status codes per [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md) §8.
- **Idempotency:** `Idempotency-Key` header required on `POST /checkout/orders` and `POST /payments/verify`. Server caches the first response and replays it for the same key within 24h.
- **Money:** all amounts are INR strings serialized as decimal numbers (e.g. `"7499.00"`), never floats.
- **IDs:** UUIDs for internal resources. `order_number` (e.g. `OOR-202605-00001`) for human references.
- **Webhooks:** prefixed `/webhooks/<provider>`. Verify provider signature before any work. Return `200 { "received": true }` quickly; defer work to a background task if needed.

---

## 2. Endpoint Map (at a glance)

| Group | Auth | Purpose |
|---|---|---|
| `/api/v1/products` | Public | Catalog browse, detail, search |
| `/api/v1/categories` | Public | Filter taxonomy |
| `/api/v1/cart` | Bearer (or session for guests via cookie — see §5) | Server-side cart for logged-in users |
| `/api/v1/checkout` | Public + Bearer | Quote totals, place order |
| `/api/v1/payments` | Public + Bearer | Razorpay order create, verify |
| `/api/v1/orders` | Public (by `order_number + email`) or Bearer | Tracking |
| `/api/v1/account/*` | Bearer | Me, addresses, wishlist, my orders |
| `/api/v1/admin/*` | Bearer + `role=admin` | Product/order/inventory/customer/analytics |
| `/api/v1/webhooks/*` | Signature-verified | Razorpay, Clerk, WhatsApp, Instagram |
| `/api/v1/health` | Public | Liveness + DB connectivity |

---

## 3. Public — Catalog

### `GET /products`

Query parameters:
- `q` — full-text search string
- `category` — repeated; category slug (`?category=kanchipuram&category=bridal`)
- `min_price`, `max_price` — INR
- `color`, `fabric` — repeated
- `sort` — `new` (default) | `price_asc` | `price_desc` | `bestseller`
- `cursor`, `page_size`

Response:
```json
{
  "items": [
    {
      "id": "uuid",
      "slug": "string",
      "name": "string",
      "primary_image_url": "string",
      "base_price": "7499.00",
      "mrp": "9999.00",
      "is_bestseller": true,
      "is_new": false,
      "available": true
    }
  ],
  "next_cursor": "string|null",
  "total": 142
}
```

### `GET /products/{slug}`

Returns full product detail. **`slug` is immutable** (PRD §7.2). If product `status = archived`, return 200 with `{ "available": false, ... }` so the frontend can render "Product Unavailable" instead of a 404 — preserves bot URL contract.

Response:
```json
{
  "id": "uuid",
  "slug": "string",
  "name": "string",
  "description": "string",
  "base_price": "7499.00",
  "mrp": "9999.00",
  "available": true,
  "status": "published",
  "images": [{ "url": "...", "alt": "...", "position": 0, "is_primary": true }],
  "variants": [
    {
      "id": "uuid",
      "color": "Maroon",
      "fabric": "Kanchipuram Silk",
      "price": "7499.00",
      "stock": 3,
      "available": true
    }
  ],
  "categories": [{ "slug": "kanchipuram", "name": "Kanchipuram Silk", "kind": "fabric" }],
  "tags": ["bridal", "south-indian"],
  "seo": {
    "title": "string",
    "description": "string",
    "json_ld": { /* Product schema.org */ }
  }
}
```

### `GET /products/{slug}/related`

Returns 8 related products (same primary fabric or category). Public.

### `GET /search?q=...`

Wrapper over `GET /products` with `q` set; separate endpoint for analytics tracking later.

---

## 4. Public — Categories

### `GET /categories`

Returns full taxonomy, grouped by `kind`:
```json
{
  "fabric":         [{ "slug": "...", "name": "...", "count": 42 }],
  "occasion":       [...],
  "region":         [...],
  "price_bracket":  [...],
  "color":          [...]
}
```
`count` is the number of currently-published products in that category. Cache for 5 minutes server-side.

---

## 5. Cart

Guest carts live in the browser (localStorage). The server only stores carts for authenticated users. Guests do not call `/cart/*` — they submit cart contents directly to `/checkout/quote` and `/checkout/orders`.

### `GET /cart` — *Bearer required*

Returns the authenticated user's cart with hydrated product/variant data.

### `POST /cart/items` — *Bearer required*

Body: `{ "variant_id": "uuid", "quantity": 1 }`. Upserts (increments if the variant already in cart).

### `PATCH /cart/items/{item_id}` — *Bearer required*

Body: `{ "quantity": 2 }`. `quantity=0` deletes.

### `DELETE /cart/items/{item_id}` — *Bearer required*

### `POST /cart/merge` — *Bearer required*

Body: `{ "items": [{ "variant_id": "uuid", "quantity": 1 }] }`. Called after login to merge the guest's localStorage cart into the user's server cart.

---

## 6. Checkout

### `POST /checkout/quote` — *Public*

Returns price breakdown for a given cart payload. No DB writes.

Body:
```json
{
  "items": [{ "variant_id": "uuid", "quantity": 1 }],
  "shipping_postal_code": "560001",
  "coupon_code": "DIWALI10"
}
```
Response:
```json
{
  "subtotal": "7499.00",
  "shipping_amount": "150.00",
  "tax_amount": "0.00",
  "discount_amount": "749.90",
  "total": "6899.10",
  "coupon": { "code": "DIWALI10", "valid": true, "discount_applied": "749.90" }
}
```

### `POST /checkout/orders` — *Public (Bearer optional)*

**Requires `Idempotency-Key` header.** Creates the order in `payment_status=pending` and returns a Razorpay order ID for the frontend to invoke the Razorpay Checkout widget. For COD, returns the order directly with no Razorpay handoff.

Body:
```json
{
  "items": [{ "variant_id": "uuid", "quantity": 1 }],
  "customer": { "email": "...", "phone": "...", "full_name": "..." },
  "shipping_address": { "recipient_name": "...", "phone": "...", "line1": "...", "city": "...", "state": "...", "postal_code": "...", "country": "IN" },
  "save_address": false,
  "payment_method": "razorpay",
  "coupon_code": "DIWALI10",
  "notes": "Gift wrap please"
}
```
Response (Razorpay):
```json
{
  "order_number": "OOR-202605-00001",
  "total": "6899.10",
  "payment": {
    "razorpay_order_id": "order_xxx",
    "razorpay_key_id": "rzp_live_xxx",
    "amount_paise": 689910
  }
}
```
Response (COD):
```json
{
  "order_number": "OOR-202605-00001",
  "total": "7049.10",
  "payment": { "method": "cod", "status": "cod_pending" }
}
```

**Server actions:** validates stock (per [INVENTORY_FLOW.md](INVENTORY_FLOW.md)), recomputes totals server-side (never trusts the client), creates the order + `order_item`s, creates a Razorpay order (if applicable), and queues nothing — the order remains `pending` until the payment webhook fires.

---

## 7. Payments

### `POST /payments/verify` — *Public*

**Requires `Idempotency-Key`.** Called by the frontend immediately after Razorpay Checkout returns success. Verifies the HMAC signature, captures the payment in our DB, decrements stock, and triggers the confirmation email.

Body:
```json
{
  "order_number": "OOR-202605-00001",
  "razorpay_order_id": "order_xxx",
  "razorpay_payment_id": "pay_xxx",
  "razorpay_signature": "sha256-hex"
}
```
Response: same as `GET /orders/{order_number}`.

> The webhook (`POST /webhooks/razorpay`) is the **source of truth**. `/payments/verify` only exists to give the customer instant feedback. If the customer closes the browser before calling it, the webhook still finalises the order.

---

## 8. Orders (tracking)

### `GET /orders/{order_number}?email=...` — *Public*

Anonymous tracking for guest orders. The `email` query parameter must match the order's stored email (rate-limited per IP to deter scraping).

Response:
```json
{
  "order_number": "OOR-202605-00001",
  "status": "shipped",
  "payment_status": "paid",
  "placed_at": "...",
  "packed_at": "...",
  "shipped_at": "...",
  "delivered_at": null,
  "items": [...],
  "shipping_address": {...},
  "totals": {...},
  "shipment": { "courier_name": "Bluedart", "tracking_id": "BD12345", "tracking_url": "..." }
}
```

### `GET /account/orders` — *Bearer*

Paginated list of the user's orders.

### `GET /account/orders/{order_number}` — *Bearer*

Full detail (same shape as public tracking).

---

## 9. Account

| Route | Method | Purpose |
|---|---|---|
| `/account/me` | GET / PATCH | Profile (name, phone) |
| `/account/addresses` | GET / POST | List / create |
| `/account/addresses/{id}` | GET / PATCH / DELETE | CRUD |
| `/account/addresses/{id}/default` | POST | Mark default |
| `/account/wishlist` | GET | List wishlisted products |
| `/account/wishlist/{product_id}` | PUT / DELETE | Add / remove |
| `/account/recently-viewed` | GET / POST | List / record a view |

---

## 10. Admin

All routes below require `Authorization: Bearer <JWT>` with `role=admin` (PRD §14: single admin only at launch).

### Products
| Route | Method | Purpose |
|---|---|---|
| `/admin/products` | GET | List with filters: status, search, category |
| `/admin/products` | POST | Create draft |
| `/admin/products/{id}` | GET / PATCH | Read / update (name change does NOT change slug — PRD §7.2) |
| `/admin/products/{id}/publish` | POST | Move draft → published |
| `/admin/products/{id}/archive` | POST | Soft-delete (preserves URL) |
| `/admin/products/{id}/images` | POST / DELETE | Attach a Cloudinary `public_id` (frontend uploads directly to Cloudinary using a signature obtained from `/admin/media/sign`) |
| `/admin/products/{id}/variants` | GET / POST | List / create variants |
| `/admin/products/{id}/variants/{vid}` | PATCH / DELETE | Update / deactivate |
| `/admin/products/csv/import` | POST | Multipart CSV upload; queues background job; returns `import_id` |
| `/admin/products/csv/imports/{import_id}` | GET | Job status + per-row errors |

### Media
| Route | Method | Purpose |
|---|---|---|
| `/admin/media/sign` | POST | Returns a Cloudinary signature so the frontend can upload directly; backend never proxies the file (avoids 30MB body bottleneck) |

### Orders
| Route | Method | Purpose |
|---|---|---|
| `/admin/orders` | GET | List with filters: status, payment_status, date range, search |
| `/admin/orders/{order_number}` | GET / PATCH | Read / update status |
| `/admin/orders/{order_number}/status` | POST | Move `placed → packed → shipped → delivered` (validates legal transitions) |
| `/admin/orders/{order_number}/shipment` | PUT | Set courier name + tracking ID + URL |
| `/admin/orders/{order_number}/cod/mark-paid` | POST | COD payment confirmation |
| `/admin/orders/{order_number}/refund` | POST | (Phase 2) Razorpay refund — not Phase 1 |
| `/admin/orders/{order_number}/cancel` | POST | Cancel + restock |

### Inventory
| Route | Method | Purpose |
|---|---|---|
| `/admin/inventory/low-stock` | GET | Variants below threshold |
| `/admin/inventory/variants/{id}/adjust` | POST | Manual stock adjustment with reason — writes `stock_movement` |
| `/admin/inventory/movements` | GET | Audit log, filterable |

### Customers
| Route | Method | Purpose |
|---|---|---|
| `/admin/customers` | GET | List with order count, total spend, join date |
| `/admin/customers/{id}` | GET | Detail + order history |

### Coupons (if Cycle 2+ accepts)
| Route | Method | Purpose |
|---|---|---|
| `/admin/coupons` | GET / POST | List / create |
| `/admin/coupons/{id}` | PATCH / DELETE | Update / deactivate |

### Analytics
| Route | Method | Purpose |
|---|---|---|
| `/admin/analytics/revenue` | GET | Today / week / month totals |
| `/admin/analytics/top-products` | GET | By order count and by revenue |
| `/admin/analytics/orders/volume` | GET | Daily/weekly volume chart |
| `/admin/analytics/traffic-sources` | GET | (NICE TO HAVE) per `bot_event` |

---

## 11. Webhooks

### `POST /webhooks/razorpay`

Verify HMAC signature using webhook secret. Switch on `event`:
- `payment.captured` → mark `payment.status=captured`, `order.payment_status=paid`, decrement stock, fire confirmation email
- `payment.failed` → mark `payment.status=failed`
- `refund.processed` → mark `payment.status=refunded`

**Idempotency:** `payment.webhook_event_id` UNIQUE prevents reprocessing.

Returns `200 { "received": true }` always, after work is complete (or queued).

### `POST /webhooks/clerk`

Verified via Svix signature headers. Events handled:
- `user.created` → insert into `user`
- `user.updated` → update profile fields
- `user.deleted` → mark user inactive (don't hard-delete; orders depend on it)

### `POST /webhooks/whatsapp` and `POST /webhooks/instagram`

**Status: UNCONFIRMED.** PRD §7 says bots are "already built and functional" but supplies no provider, no auth scheme, and no payload examples. Two design paths exist:

1. **Bots only redirect users to URLs.** No backend webhook needed; the website handles inbound traffic naturally. This is the simplest path and aligns literally with PRD §7.
2. **Bots also ping the backend on each redirect** for traffic-source analytics. Endpoint shape TBD; rough spec:
   ```
   POST /webhooks/{provider}
   { "event": "redirect", "slug": "...", "external_user_ref": "hashed-id", "metadata": {...} }
   ```
   Writes a `bot_event` row.

Defer implementation until Dev 3 confirms which path applies. See [WHATSAPP_AUTOMATION.md](WHATSAPP_AUTOMATION.md).

---

## 12. Health & Ops

### `GET /health`

```json
{ "status": "ok", "db": "ok", "version": "v0.1.0", "commit": "abc123" }
```
DB check is a `SELECT 1`. Returns 503 if DB unreachable. Used by Railway/Render health checks.

### `GET /health/live` — liveness only (no DB hit), used by orchestrator.

---

## 13. Rate Limiting Defaults

| Route group | Limit (per IP, anon) | Limit (per user, auth) |
|---|---|---|
| `GET /products*`, `GET /categories` | 120 / min | 240 / min |
| `GET /search` | 60 / min | 120 / min |
| `POST /cart/*`, `POST /checkout/*` | 30 / min | 60 / min |
| `POST /payments/verify` | 20 / min | 40 / min |
| `GET /orders/{number}` (anon tracking) | 30 / min | n/a |
| Webhooks | unlimited (signature-gated) | n/a |
| `/admin/*` | n/a | 600 / min |

Implemented via `slowapi` with Redis backend (once Redis is available; in-memory at launch is acceptable for single-instance Railway).

---

## 14. Open API-Contract Questions

| Question | Default if unanswered |
|---|---|
| Cursor-based vs offset pagination? | Offset for admin lists; cursor (timestamp+id) for `/products` |
| Should `/checkout/quote` recompute stock availability? | Yes — return `unavailable_items: []` so the frontend can flag them before checkout |
| Public exposure of `product.id`? | Yes — internal UUID returned; frontend should still link via `slug` |
| Coupon validation endpoint separate from `/checkout/quote`? | No — fold into `/checkout/quote` to avoid double-call |
| Anonymous tracking auth — email match only, or also order_number + last 4 of phone? | Email match for launch; revisit if abuse occurs |
