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
