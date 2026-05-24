# CURRENT_STATUS.md

**Last updated:** 2026-05-24 (post-Cycle 0 scaffold)
**Backend cycle:** Cycle 0 complete — scaffold + foundation in place; no business features yet

> This document is the single source of truth for "what is actually built right now". All other ai-context docs describe **planned** architecture. This one describes **shipped** reality. Update it at the end of every working session.

---

## 1. TL;DR

Cycle 0 is done. The repo now contains a production-grade FastAPI scaffold:

- Layered package (`app/core`, `app/db`, `app/models`, `app/repositories`, `app/services`, `app/schemas`, `app/routers`, `app/api/v1`, `app/integrations`, `app/tasks`, `app/utils`) wired together by an application factory.
- Async SQLAlchemy 2.x + Alembic with one baseline migration enabling `pgcrypto`.
- Clerk JWT verification foundation (`app/core/security.py`) with JWKS cache and FastAPI dependencies (`get_current_principal_optional`, `get_current_principal`, `require_admin`).
- structlog JSON logging + request-context middleware.
- Health endpoints (`/api/v1/health`, `/api/v1/health/live`).
- Dockerfile (multi-stage, non-root) + docker-compose for local Postgres.
- pytest scaffold with httpx ASGI client.
- `pyproject.toml` (uv-managed) with ruff + mypy strict.
- `.env.example`, `.gitignore`, `.dockerignore`.

**Implementation progress: ~10% (Cycle 0 of 5; foundation only, zero business features).**

Decisions locked in this cycle:
- **Hosting:** Railway (backend), Neon (database).
- **Tooling:** uv for dependency management, ruff for lint/format, mypy strict for type checks.

---

## 2. What Exists

| Area | Status | Notes |
|---|---|---|
| PRD | Complete (v1.0) | Reformatted for readability on 2026-05-24 |
| Architecture spec (this folder) | Complete | Forward-looking, derived from PRD |
| FastAPI app scaffold | ✅ Cycle 0 | `app/main.py` with factory + lifespan |
| `pyproject.toml` (uv) | ✅ Cycle 0 | Python 3.12+, ruff + mypy strict configured |
| Database engine + session | ✅ Cycle 0 | `app/db/session.py` async engine with pool config |
| Alembic | ✅ Cycle 0 | `alembic/env.py` reads from settings; baseline migration `0001_enable_extensions` |
| Database schema (domain tables) | ☐ Cycle 1+ | Spec in [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md); models will land in cycles 1–4 |
| Clerk JWT verification | ✅ Cycle 0 (foundation) | Verifier + DI deps; user model + webhook handler land in Cycle 4 |
| Clerk webhook signature verifier | ✅ Cycle 0 | `app/integrations/clerk.py` |
| Razorpay integration | ☐ Cycle 2 | — |
| Cloudinary integration | ☐ Cycle 1 | — |
| WhatsApp / Instagram webhook | ☐ Cycle 6+ (optional) | Spec in [WHATSAPP_AUTOMATION.md](WHATSAPP_AUTOMATION.md) |
| Email (Resend) integration | ☐ Cycle 2 | — |
| CI/CD | ☐ Cycle 0.5 | Local pytest + ruff + mypy work; GitHub Actions to add |
| Hosting | ⏳ Provisioning | **Railway** chosen; create project + first deploy in next pass |
| Managed Postgres | ⏳ Provisioning | **Neon** chosen; create DB + use pooled connection string |
| Domain | ☐ | Open Item #1 in PRD §15 |

---

## 3. Cycle Progress (per PRD §12)

| Cycle | Name | Backend Scope | Status |
|---|---|---|---|
| Cycle 0 | Foundation | Repo, DB schema, infra, CI/CD, Clerk auth wiring | ⏳ In progress — scaffold done; infra provisioning + first deploy pending |
| Cycle 1 | Core Catalog | Product model, product/catalog APIs, Cloudinary pipeline, bot URL contract | ☐ Not started |
| Cycle 2 | Commerce | Cart, checkout, Razorpay backend (incl. COD), order create, email | ☐ Not started |
| Cycle 3 | Admin Dashboard | Product CRUD, CSV bulk upload, order mgmt, inventory, customer list, analytics | ☐ Not started |
| Cycle 4 | User Accounts | Clerk webhook, wishlist, order history, saved addresses, recently viewed | ☐ Not started |
| Cycle 5 | Launch Readiness | Tracking endpoints, perf audit, prod hardening, go-live | ☐ Not started |
| Cycle 6+ | Post-Launch | Shiprocket / Delhivery API, analytics, etc. | ☐ Not in current scope |

---

## 4. Hard Blockers (must be resolved before / during Cycle 0)

These are open items from PRD §15 that block backend work specifically:

| # | Blocker | Blocks |
|---|---|---|
| 1 | Final saree taxonomy (fabric, occasion, region, price brackets) | DB schema for categories/tags, filter API |
| 2 | First batch of real product data (CSV) | End-to-end testing of catalog, search, bot redirect |
| 3 | Shipping zones + charges | Checkout shipping calculation, order total math |
| 4 | Returns & refund policy text | Razorpay refund flow, terms-of-service gate |
| 5 | Confirmed domain name | Clerk allowed origins, Razorpay webhook URL, CORS config |
| 6 | Confirmed bot webhook spec (WhatsApp Cloud API vs. custom) | [WHATSAPP_AUTOMATION.md](WHATSAPP_AUTOMATION.md) — currently inferred from PRD §7 |
| 7 | Returns flow decision | Out of Phase 1 per PRD §14, but Razorpay still needs a refund handler for payment failures |

---

## 5. Decisions Locked In (per PRD)

These are confirmed and do not require re-litigation:

- **Stack:** FastAPI + PostgreSQL + SQLAlchemy + Alembic
- **Auth:** Clerk (JWT verification on backend)
- **Payments:** Razorpay only (UPI, cards, NetBanking, wallets, COD)
- **Media:** Cloudinary (server-signed uploads, WebP delivery via CDN)
- **Email:** Resend (transactional, ≤3000/mo at launch)
- **Hosting:** Railway or Render (decision deferred to Cycle 0; both acceptable)
- **DB hosting:** Supabase or Neon (decision deferred to Cycle 0; both acceptable)
- **Search:** Postgres Full-Text Search at launch; Algolia is Phase 2+
- **Scope:** No mobile app, no i18n, no blog, no returns flow, no multi-admin (per PRD §14)

---

## 6. Decisions Still Open

| Decision | Owner | Needed By |
|---|---|---|
| ~~Railway vs. Render for backend hosting~~ | — | **Railway** (decided 2026-05-24) |
| ~~Supabase vs. Neon for managed Postgres~~ | — | **Neon** (decided 2026-05-24) |
| WhatsApp provider — Meta Cloud API direct vs. existing custom webhook | Bot dev | Cycle 1 |
| Instagram bot mechanism — Meta Graph API vs. third-party | Bot dev | Cycle 1 |
| Background job runner — none (BackgroundTasks), Celery+Redis, or Arq | Backend dev | Cycle 1 (when image upload pipeline lands) |
| Error tracking — Sentry vs. none at launch | Backend dev | Cycle 5 |

---

## 7. Risks Known at this Stage

- **Deadline:** 8 days, 3 devs, full e-commerce. Per PRD §12 itself: "achievable only with strict parallel work". Any scope creep kills the date.
- **Bot webhook unknown:** PRD states bots are "already built and functional" but provides no contract, no auth scheme, and no payload examples. Backend work on the webhook endpoint is blocked until Dev 3 hands over a spec.
- **Cloudinary upload size:** PRD §5.4 specifies 10–30MB raw uploads. FastAPI default and Railway/Render request body limits must be verified before admin upload feature is built.
- **Razorpay webhook idempotency:** Not mentioned in PRD; must be enforced at the backend regardless (see [API_CONTRACTS.md](API_CONTRACTS.md) §Payments).
- **No staging environment defined:** PRD §9 only describes a production architecture. A staging tier needs to be added in Cycle 0 (see [DEPLOYMENT.md](DEPLOYMENT.md)).

---

## 8. How to Update This File

When you finish work, update only the cells that changed. Keep entries short. Move detailed decisions into the relevant doc (e.g. [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md)) and just reference them here.
