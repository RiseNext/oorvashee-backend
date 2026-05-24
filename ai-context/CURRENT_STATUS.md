# CURRENT_STATUS.md

**Last updated:** 2026-05-25 (post Phase 2G — production hardening live)
**Backend cycle:** Cycle 0 ✅ · Cycle 1 catalog ✅ · **Cycle 2 commerce ✅ (cart + wishlist + checkout + Razorpay + Clerk + Cloudinary + hardening). Phase 3 admin dashboard next.**

> Source of truth for "what is actually shipped." Other docs describe planned shape; this one describes reality. Update at the end of every session.

---

## 1. TL;DR

Infrastructure is live and the full domain schema is in place.

**Live:**
- Railway deploy (FastAPI behind Gunicorn + UvicornWorker) — auto-deploy from `main`.
- Neon Postgres connected via pooled connection string; `/api/v1/health` returns `{ "status": "ok", "db": "connected" }`.
- Docker build + non-root runtime + healthcheck.
- Swagger / OpenAPI at `/docs` (non-prod) and `/openapi.json`.
- Clerk JWT verifier (JWKS cache + `require_admin` dependency).
- structlog JSON logging + request-id middleware.

**This session shipped:**
- All 24 domain tables modelled as SQLAlchemy 2.x ORM (users, roles, addresses, catalog, inventory, commerce, engagement, audit).
- 14 native Postgres ENUM types.
- Hand-written initial migration `0002_initial_schema.py` (CREATE-only, idempotent, reviewable). Seeds the three system roles (`customer`, `admin`, `staff`).
- Reusable infrastructure: `BaseService`, `BaseRepository[T]`, offset + cursor pagination, audit-log writer (`AuditService`).
- Catalog read APIs end-to-end (`GET /api/v1/products`, `GET /api/v1/products/{slug}`, `GET /api/v1/categories`) — proves the router → service → repository → model layering works against the new schema.
- Test coverage: 7 tests passing (health, config, models registry).

**This session (Phase 2G — production hardening) shipped:**
- **Rate limiting** via slowapi — global default `200/minute`, per-profile stricter caps: `checkout` 30/min, `tracking` 30/min, `webhook` 120/min, `admin` 600/min. Decorated onto `/checkout/orders`, `/payments/verify`, `/orders/{number}`, `/webhooks/razorpay`, `/webhooks/clerk`. JWT-aware key function (`composite_key`) — authenticated traffic shares per-user buckets, anonymous falls back to IP. Storage URI env-driven (`memory://` today; `redis://...` for horizontal scaling). Custom 429 handler matches the `{detail, code, request_id}` error shape. Verified live: `/checkout/orders` rejects request #31 with `code=rate_limit_exceeded`.
- **Sentry SDK** — optional async-safe init in lifespan; lazy + defensive against empty/garbage DSN. FastAPI + Starlette integrations. PII stripped. `request_id` propagated as Sentry tag from middleware. Sample rates env-driven (`SENTRY_TRACES_SAMPLE_RATE=0.1` default).
- **Security headers middleware** — HSTS (1y + subdomains), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`, `X-Permitted-Cross-Domain-Policies`, `Server: oorvashee`. Applied to 4xx/5xx too.
- **Body size limit middleware** — 1 MB JSON cap (Content-Length fast path), webhook routes exempt via path prefix list. Returns 413 `request_body_too_large` with request_id.
- **Request-ID propagation** — incoming `X-Request-ID` honoured (≤64 chars), generated if missing, echoed back on response, bound to structlog contextvars, set as Sentry tag.
- **Latency banding** in access log — every `http_request` line carries `latency_band=fast|ok|slow|very_slow` for instant dashboarding.
- **`scripts/cleanup_idempotency.py`** — Railway-cron compatible. Batched DELETE (default 500/batch) on `request_idempotency` rows older than 24h. Verified live against Neon.
- **17 new unit tests** (137 total): security headers (success + 4xx surfaces), body size limit (oversize / valid / webhook exempt / malformed Content-Length), JWT-aware rate limit key function (peek-without-verify, IP fallback, malformed bearer, lowercase scheme).

**Earlier session (Phase 2F) shipped:**
- **`CloudinaryClient`** — async pure-HMAC signer + verifier + `destroy()`; no official Cloudinary SDK (sync-only). Builds `SignedUploadEnvelope` carrying all constraints inside the signature (folder, max_file_size, allowed_formats, eager). Cloudinary enforces every constraint at the edge — Railway never sees rejected bytes.
- **`MediaService`** — per-context configuration (`CONTEXT_CONFIGS`) for `product`, `category`, `banner` plus future-reserved `reel`, `influencer`, `whatsapp_catalog`. Each has tailored folder template / size cap / allowed formats / eager transformation set. Adding a new context = enum value + dict entry.
- **Pre-generated eager transformations** for CDN cache warming: product card (320×400), card HiDPI (640×800), PDP main (1280×1600). Banner: desktop hero (1920×720), tablet, mobile (768×1024). All use `f_auto` (AVIF→WebP→JPEG by client) + `q_auto` quality.
- **Admin routes** under `/api/v1/admin`:
  - `POST /media/sign` — issues signed upload envelope (admin-only via `require_role("admin")`)
  - `POST /products/{id}/images` — attaches Cloudinary-uploaded image after verifying response signature + folder ownership
  - `DELETE /products/{id}/images/{image_id}` — removes DB row + best-effort Cloudinary destroy + promotes next image to primary if needed
- **Schema additions** (migration 0004, additive): `product_images.format`, `product_images.bytes`, `categories.image_public_id`, `banners.{image,mobile_image,video}_public_id`.
- **53 new unit tests** (120 total passing): signature canonicalisation (skip-fields rules, pipe-delimited lists, alphabetical order), upload-response verification, context config validity (eager transformation grammar, format-vs-resource-type sanity, WhatsApp 5MB cap), folder-ownership guard against cross-context tampering.
- **Live verified against Neon:** sign endpoint emits signature that matches independent recompute byte-for-byte; cross-context payload rejected at 400 with `asset_folder_mismatch` code.

**Earlier session (Phase 2E) shipped:**
- **`POST /api/v1/webhooks/clerk`** — Svix-signature-verified endpoint receiving `user.created` / `user.updated` / `user.deleted`. Other event types are 200-ignored. Replay-protected (Svix rejects events >5 min old). End-to-end verified against live Neon: create → idempotent replay → role update → bad-sig 401 → soft-delete.
- **`UserSyncService`** — idempotent create/update/delete. Auto-creates `UserProfile`, mirrors single role from `public_metadata.role` into `user_roles` junction (auto-creates unknown role names with `is_system=false`). Delete = soft-delete (`deleted_at` + email scrub to `deleted+<id>@oorvashee.invalid` + revoke roles) — preserves FK integrity from `orders`, `audit_logs`, `stock_movements`.
- **`require_role(*roles)` dependency factory** — scalable RBAC gate. `require_admin` preserved for Cycle-0 call sites. Both case-insensitive on role names; read from JWT claim (no DB hit).
- **Auto-provisioning fallback clarified** — the existing `get_current_user` dep stays as defensive fallback against webhook lag; the webhook is now the primary path.
- 67 unit tests passing (+12 new: ClerkUser primary-email/phone/role/full-name derivation, role normalisation, factory edge cases, backwards-compat for `require_admin`).

**Earlier session (Phase 2A–2D) shipped:**
- **Cart system** — `app/services/cart_service.py` (add, update, remove, clear, merge), `app/repositories/cart_repo.py`, `app/routers/account/cart.py`, `app/schemas/cart.py`. 6 endpoints under `/api/v1/account/cart`.
- **Wishlist system** — service + repo + router + schemas. 4 endpoints under `/api/v1/account/wishlist` (including `move-to-cart`).
- **Checkout engine** with enterprise-grade locking — `app/services/checkout_service.py` + `app/services/inventory_service.py` (SELECT ... FOR UPDATE on deterministically-ordered inventory rows; reserve-on-create for Razorpay, decrement-on-create for COD; releases reservation on payment.failed).
- **Razorpay integration** — `app/integrations/razorpay_client.py` (async httpx, no SDK), HMAC-verified webhook handler with replay protection via `payments.webhook_event_id` UNIQUE, `/payments/verify` for instant frontend confirmation.
- **Idempotency middleware** — new `request_idempotency` table + `app/core/idempotency.py`. POSTs to `/checkout/orders` and `/payments/verify` require `Idempotency-Key`; replay returns the cached response (no double-charge, no double-create).
- **Auto user provisioning** — `get_current_user` dependency creates the local `users` row on first authenticated request (Cycle 4 Clerk webhook becomes the primary path; dep stays as fallback).
- **Order tracking** — `/orders/{number}?email=` (guest) and `/account/orders` (authenticated).
- **Unit tests** — 46 passing: cart logic, checkout logic, Razorpay signature, idempotency middleware, inventory lock ordering, order-number entropy.

Verified end-to-end against live Neon: cart add → checkout quote → place COD order → idempotency replay → tracking. Inventory atomically decremented, audit logged, stock movement recorded.

**Earlier session (seed system) shipped:**
- `app/seeds/` package — `base.py` (helpers), `data.py` (declarative seed data), `runner.py` (orchestration). 33 categories, 10 sarees with 24 variants + 24 placeholder images + 53 category links, 4 banners.
- `scripts/seed_dev.py` CLI — `run` / `reset --yes` / `reseed --yes` / `status`, all refusing if `APP_ENV=prod`.
- Idempotency on natural keys (slug, SKU, title+placement); re-running mutates only changed `update_fields`.
- Found + fixed two latent model bugs while bringing seeds online: `User.roles` foreign_keys ambiguity, and the SQLAlchemy `postgresql.ENUM` default-to-`.name` issue (now wrapped by `app.models.enums.pg_enum`).
- Catalog APIs (`/products`, `/products/{slug}`, `/categories`) verified end-to-end against the seeded Neon DB.

**Implementation progress: ~40%.** Foundation + schema + catalog reads done. Commerce writes, admin, integrations remain.

---

## 2. What Exists

| Area | Status | File / Note |
|---|---|---|
| FastAPI app + factory | ✅ | [app/main.py](../app/main.py) |
| Settings + .env | ✅ | [app/core/config.py](../app/core/config.py) |
| Structured logging | ✅ | [app/core/logging.py](../app/core/logging.py) |
| Request middleware | ✅ | [app/core/middleware.py](../app/core/middleware.py) |
| Global exception handlers | ✅ | [app/core/exceptions.py](../app/core/exceptions.py) |
| Pagination (offset + cursor) | ✅ | [app/core/pagination.py](../app/core/pagination.py) |
| Clerk JWT verifier + DI | ✅ | [app/core/security.py](../app/core/security.py) |
| Clerk webhook signature verifier | ✅ | [app/integrations/clerk.py](../app/integrations/clerk.py) |
| Async SQLAlchemy engine + session | ✅ | [app/db/session.py](../app/db/session.py) |
| `Base` + mixins (UUID PK, timestamps, soft delete, auditable) | ✅ | [app/db/base.py](../app/db/base.py) |
| Health endpoints | ✅ | [app/routers/health.py](../app/routers/health.py) |
| **ORM models — all 24 tables** | ✅ | [app/models/](../app/models/) |
| **Initial schema migration** | ✅ | [alembic/versions/...0002_initial_schema.py](../alembic/versions/) |
| Postgres ENUMs (14) | ✅ | Created in `0002_initial_schema.py` |
| BaseRepository, BaseService | ✅ | [app/repositories/base.py](../app/repositories/base.py), [app/services/base.py](../app/services/base.py) |
| Audit log writer | ✅ | [app/services/audit_service.py](../app/services/audit_service.py) |
| Catalog reads (list + detail + categories) | ✅ | [app/routers/public/](../app/routers/public/) |
| Seed system (`scripts/seed_dev.py`) | ✅ | [app/seeds/](../app/seeds/) — 33 cats + 10 products + 4 banners; idempotent; prod-safe |
| Cart APIs (6 endpoints) | ✅ 2A | [app/routers/account/cart.py](../app/routers/account/cart.py) |
| Wishlist APIs (4 endpoints) | ✅ 2B | [app/routers/account/wishlist.py](../app/routers/account/wishlist.py) |
| Checkout engine (quote + place, FOR UPDATE locking) | ✅ 2C | [app/services/checkout_service.py](../app/services/checkout_service.py) + [inventory_service.py](../app/services/inventory_service.py) |
| Stock reservation model (Razorpay) + immediate decrement (COD) | ✅ 2C | Per [INVENTORY_FLOW.md](INVENTORY_FLOW.md) §4 |
| Razorpay integration (client + verify + webhook) | ✅ 2D | [app/integrations/razorpay_client.py](../app/integrations/razorpay_client.py) + [services/payment_service.py](../app/services/payment_service.py) |
| Idempotency middleware (`Idempotency-Key`) | ✅ 2D | [app/core/idempotency.py](../app/core/idempotency.py); table `request_idempotency` |
| Order tracking (guest + account) | ✅ | [routers/public/orders.py](../app/routers/public/orders.py), [account/orders.py](../app/routers/account/orders.py) |
| Auto user provisioning on first authed call | ✅ | `get_current_user` in [security.py](../app/core/security.py) |
| Cloudinary signed upload + product image attach/delete | ✅ 2F | [integrations/cloudinary_client.py](../app/integrations/cloudinary_client.py) + [services/media_service.py](../app/services/media_service.py) + [routers/admin/media.py](../app/routers/admin/media.py) |
| Banner/category image attach endpoints | ☐ Phase 3 | Sign endpoint already supports these contexts; attach lands with admin CRUD |
| Clerk webhook (`/webhooks/clerk` via Svix) | ✅ 2E | [routers/webhooks/clerk.py](../app/routers/webhooks/clerk.py) + [services/user_sync_service.py](../app/services/user_sync_service.py) |
| `require_role(*)` factory | ✅ 2E | [core/security.py](../app/core/security.py) |
| Resend email | ☐ Cycle 2 | Next turn (queue confirmation on order.placed) |
| Admin product CRUD | ☐ Cycle 3 | — |
| CSV bulk import | ☐ Cycle 3 | — |
| Rate limiting middleware (slowapi) | ✅ 2G | [core/rate_limit.py](../app/core/rate_limit.py); Redis-ready via env |
| Sentry error tracking | ✅ 2G | [core/sentry.py](../app/core/sentry.py); optional, guarded |
| Security headers middleware | ✅ 2G | [core/security_headers.py](../app/core/security_headers.py) |
| Body size limit middleware | ✅ 2G | [core/body_size.py](../app/core/body_size.py) |
| Idempotency cleanup cron | ✅ 2G | [scripts/cleanup_idempotency.py](../scripts/cleanup_idempotency.py) |
| Request-ID echo + Sentry tag propagation | ✅ 2G | Updated [core/middleware.py](../app/core/middleware.py) |
| CI / GitHub Actions | ☐ | Local pytest + ruff + mypy already work |

---

## 3. Cycle Progress (per PRD §12)

| Cycle | Name | Status |
|---|---|---|
| Cycle 0 | Foundation — scaffold, infra, deploy, health | ✅ Complete |
| Cycle 1 | Core Catalog — schema + catalog reads | ⏳ ~70% (schema ✅ · reads ✅ · Cloudinary, slug-stability tests, admin writes next) |
| Cycle 2 | Commerce — cart, checkout, Razorpay, order, email | ☐ |
| Cycle 3 | Admin Dashboard — CRUD, CSV, inventory, customers, analytics | ☐ |
| Cycle 4 | User Accounts — Clerk webhook, wishlist, history, addresses | ☐ (models in place; APIs pending) |
| Cycle 5 | Launch Readiness — tracking, hardening, Sentry, go-live | ☐ |

---

## 4. Decisions Locked In

- **Hosting:** Railway (backend), Neon (Postgres, pooled).
- **Stack:** FastAPI · SQLAlchemy 2.x async · Alembic · Pydantic v2 · structlog · uv.
- **Naming:** plural snake_case table names; singular PascalCase models. Constraint names follow the `NAMING_CONVENTION` in [app/db/base.py](../app/db/base.py).
- **Enums:** Postgres native ENUM types (14 total) for closed sets; freeform string columns for evolving sets.
- **Money:** `Numeric(12, 2)` INR throughout. Never float.
- **PKs:** UUID v4 via `gen_random_uuid()` (pgcrypto extension, enabled by migration 0001).
- **Soft delete:** `deleted_at` on entities where a row may hide-but-survive (users, addresses, categories, reviews, banners). Hard never on commerce rows; status enums instead.
- **Bot URL contract:** product slugs are immutable; `archived` products return 200 with `available=false` (preserved in `GET /products/{slug}`).
- **Stock locking:** decrements use `SELECT ... FOR UPDATE` on `inventory` row (to be enforced in checkout service in Cycle 2).
- **Webhook idempotency:** UNIQUE on `payments.webhook_event_id`.

---

## 5. Decisions Still Open

| Decision | Owner | Needed By |
|---|---|---|
| WhatsApp / Instagram bot webhook spec | Dev 3 | Cycle 6+ (optional) |
| Background job runner — BackgroundTasks vs Arq vs Celery | Backend | Cycle 2 (when emails land) |
| Sentry vs no error tracking at launch | Backend | Cycle 5 |
| Coupon / cart soft-reservation policy (PRD §11 ambiguous) | PM | Cycle 2 |
| CSV format for bulk import (column names + per-row variant block) | PM | Cycle 3 |

---

## 6. Risks

- **Deadline:** 4 days to 29 May launch. Each remaining cycle must stay scope-tight.
- **Cloudinary upload size:** PRD §5.4 says 10–30 MB raw uploads. Railway / asyncpg default body limits to be verified before admin image flow.
- **Bot webhook unknown:** still no contract from Dev 3 — Option A (URLs only, no backend webhook) remains the working assumption.
- **Single-instance backend:** in-memory rate limit state diverges as soon as we scale to 2 replicas. Add Upstash Redis when scaling out (not before).

---

## 7. How to Run Locally

```powershell
cd "c:\progromming\Oorvashee backend\BACKEND"
uv sync
Copy-Item .env.example .env   # fill DATABASE_URL + CLERK_*

# Option A — local Postgres in Docker:
docker compose up -d postgres
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# Option B — point .env at Neon directly (same migration command works)

# Tests + lint + typecheck:
uv run pytest -q
uv run ruff check app
uv run mypy app
```

Routes (22 total — see `/openapi.json`):

Public catalog + checkout:
- `GET    /api/v1/products` · `GET /api/v1/products/{slug}` · `GET /api/v1/categories`
- `POST   /api/v1/checkout/quote` · `POST /api/v1/checkout/orders` (Idempotency-Key required)
- `POST   /api/v1/payments/verify` (Idempotency-Key required)
- `GET    /api/v1/orders/{order_number}?email=` (guest tracking)

Authenticated (Bearer Clerk JWT):
- `GET / POST / PATCH / DELETE  /api/v1/account/cart` and `/items[/{id}]`
- `POST   /api/v1/account/cart/merge`
- `GET    /api/v1/account/wishlist` · `PUT / DELETE /{product_id}` · `POST /{product_id}/move-to-cart`
- `GET    /api/v1/account/orders` · `GET /api/v1/account/orders/{order_number}`

Webhooks (signature-verified, no JWT):
- `POST   /api/v1/webhooks/razorpay` (HMAC `X-Razorpay-Signature`)

Health:
- `GET    /api/v1/health` · `GET /api/v1/health/live`
- `GET    /docs` (Swagger; disabled in prod)

Seed CLI:
```powershell
uv run python -m scripts.seed_dev status
uv run python -m scripts.seed_dev run
uv run python -m scripts.seed_dev reset --yes      # destructive
uv run python -m scripts.seed_dev reseed --yes     # reset + run
# All commands refuse if APP_ENV=prod.
```
