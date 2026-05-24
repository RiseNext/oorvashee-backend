# CURRENT_STATUS.md

**Last updated:** 2026-05-25 (post-seed system; dev catalog live on Neon)
**Backend cycle:** Cycle 0 ✅ · Cycle 1 (catalog) ⏳ partial · Cycles 2–5 ☐

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

**This session (seed system) shipped:**
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
| Cart / wishlist / checkout APIs | ☐ Next | — |
| Razorpay integration | ☐ Cycle 2 | — |
| Cloudinary integration | ☐ Cycle 1 next pass | Signature endpoint + admin product images |
| Resend email | ☐ Cycle 2 | — |
| Admin product CRUD | ☐ Cycle 3 | — |
| CSV bulk import | ☐ Cycle 3 | — |
| Clerk webhook (`/webhooks/clerk`) | ☐ Cycle 4 | User row sync |
| Rate limiting middleware | ☐ Cycle 5 | — |
| Sentry error tracking | ☐ Cycle 5 | — |
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

Routes:
- `GET  /api/v1/health` · `GET /api/v1/health/live`
- `GET  /api/v1/products` (filters: `q, category, min_price, max_price, sort, page, page_size`)
- `GET  /api/v1/products/{slug}`
- `GET  /api/v1/categories`
- `GET  /docs` (Swagger; disabled in prod)

Seed CLI:
```powershell
uv run python -m scripts.seed_dev status
uv run python -m scripts.seed_dev run
uv run python -m scripts.seed_dev reset --yes      # destructive
uv run python -m scripts.seed_dev reseed --yes     # reset + run
# All commands refuse if APP_ENV=prod.
```
