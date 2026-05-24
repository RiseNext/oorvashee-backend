# BACKEND_ARCHITECTURE.md

> **Status:** PLANNED — nothing in this document is implemented yet. This is the target architecture for the FastAPI backend, derived strictly from PRD v1.0.
> **Authoritative current state:** [CURRENT_STATUS.md](CURRENT_STATUS.md).

---

## 1. Stack (locked, per PRD §8.1)

| Layer | Choice |
|---|---|
| Web framework | FastAPI (async, Python 3.12+) |
| ASGI server | Uvicorn behind Gunicorn (`uvicorn.workers.UvicornWorker`) in production |
| ORM | SQLAlchemy 2.x (async, `asyncpg` driver) |
| Migrations | Alembic |
| Database | PostgreSQL 15+ (Supabase or Neon) |
| Validation / serialization | Pydantic v2 |
| Auth | Clerk (JWT verified via JWKS) |
| Payments | Razorpay (SDK + webhook) |
| Media | Cloudinary (signed direct uploads; backend issues signatures) |
| Email | Resend |
| Settings | `pydantic-settings` with `.env` per environment |
| HTTP client (outbound) | `httpx` (async) |
| Logging | `structlog` JSON output |
| Background tasks (launch) | FastAPI `BackgroundTasks` (in-process) |
| Background tasks (post-launch) | Arq or Celery + Redis — see §10 |

---

## 2. Clean Architecture — Layering

Four hard layers. No layer may import from a layer above it.

```
┌────────────────────────────────────────────────────────────┐
│ ROUTERS  (HTTP only — parse, authorize, delegate)          │
│   ↓                                                         │
│ SERVICES (business logic, orchestration, transactions)     │
│   ↓                                                         │
│ REPOSITORIES (DB queries, no business rules)               │
│   ↓                                                         │
│ MODELS (SQLAlchemy ORM mappings)                           │
└────────────────────────────────────────────────────────────┘

  Cross-cutting: SCHEMAS (Pydantic), DEPENDENCIES (DI), CORE (config, security, logging)
```

**Rules (engineering rule #5 in master prompt):**

- Routers must contain **zero** business logic. Their only job is: extract request → call a service → shape a response.
- Services own all business rules, validation that depends on DB state, transactional boundaries, and side-effects (email, webhook calls, payment intents).
- Repositories are the only place that builds SQL/SQLAlchemy queries. Services never write `session.execute(select(...))` directly.
- Models never import from services, routers, or schemas. They are pure data shape + relationships.
- Pydantic schemas are split: `*Create`, `*Update`, `*Read` (response). Never re-use ORM models as response bodies.

---

## 3. Folder Structure (target)

```
BACKEND/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory, middleware, lifespan
│   │
│   ├── core/
│   │   ├── config.py            # pydantic-settings BaseSettings
│   │   ├── security.py          # Clerk JWT verification, role checks
│   │   ├── logging.py           # structlog setup
│   │   ├── exceptions.py        # Domain exceptions + global handlers
│   │   └── pagination.py        # Cursor/offset pagination helpers
│   │
│   ├── db/
│   │   ├── base.py              # DeclarativeBase
│   │   ├── session.py           # async engine + session factory
│   │   └── deps.py              # get_db() dependency
│   │
│   ├── models/                  # SQLAlchemy ORM models — one file per domain
│   │   ├── user.py
│   │   ├── product.py
│   │   ├── category.py
│   │   ├── cart.py
│   │   ├── order.py
│   │   ├── payment.py
│   │   ├── address.py
│   │   ├── wishlist.py
│   │   ├── coupon.py
│   │   ├── shipment.py
│   │   ├── stock_movement.py
│   │   └── bot_event.py
│   │
│   ├── schemas/                 # Pydantic — mirrors models/ structure
│   │   ├── user.py
│   │   ├── product.py
│   │   └── ...
│   │
│   ├── repositories/            # DB access only — pure CRUD + queries
│   │   ├── base.py              # Generic Repository[Model] base class
│   │   ├── product_repo.py
│   │   ├── order_repo.py
│   │   └── ...
│   │
│   ├── services/                # Business logic
│   │   ├── product_service.py
│   │   ├── catalog_service.py
│   │   ├── cart_service.py
│   │   ├── checkout_service.py
│   │   ├── order_service.py
│   │   ├── payment_service.py   # Razorpay orchestration
│   │   ├── inventory_service.py
│   │   ├── media_service.py     # Cloudinary signature + ingest
│   │   ├── email_service.py     # Resend templates
│   │   ├── bot_service.py       # Bot URL resolution + lead capture
│   │   └── admin/
│   │       ├── product_admin_service.py
│   │       ├── csv_import_service.py
│   │       └── analytics_service.py
│   │
│   ├── routers/                 # FastAPI APIRouter modules
│   │   ├── public/
│   │   │   ├── products.py
│   │   │   ├── categories.py
│   │   │   ├── cart.py
│   │   │   ├── checkout.py
│   │   │   ├── orders.py
│   │   │   └── search.py
│   │   ├── account/             # Authenticated customer-facing
│   │   │   ├── me.py
│   │   │   ├── addresses.py
│   │   │   ├── wishlist.py
│   │   │   └── orders.py
│   │   ├── admin/               # Admin-only
│   │   │   ├── products.py
│   │   │   ├── orders.py
│   │   │   ├── inventory.py
│   │   │   ├── customers.py
│   │   │   ├── csv.py
│   │   │   └── analytics.py
│   │   └── webhooks/
│   │       ├── razorpay.py
│   │       ├── clerk.py
│   │       ├── whatsapp.py
│   │       └── instagram.py
│   │
│   ├── integrations/            # Thin SDK wrappers — one file per provider
│   │   ├── clerk.py
│   │   ├── razorpay_client.py
│   │   ├── cloudinary_client.py
│   │   └── resend_client.py
│   │
│   ├── tasks/                   # Background jobs (BackgroundTasks now, queue later)
│   │   ├── send_email.py
│   │   ├── process_image.py
│   │   └── csv_import.py
│   │
│   └── utils/
│       ├── slugify.py
│       ├── ids.py               # ULID / order number generator
│       └── money.py             # Decimal-safe currency math
│
├── alembic/
│   ├── env.py
│   └── versions/
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   └── integration/
│
├── scripts/
│   ├── seed_dev.py
│   └── import_products.py
│
├── .env.example
├── alembic.ini
├── pyproject.toml               # or requirements.txt + requirements-dev.txt
├── Dockerfile
├── docker-compose.yml           # Postgres + app for local dev
└── README.md
```

---

## 4. Request Lifecycle

```
Client request
   ↓
[ASGI: Uvicorn]
   ↓
[Middleware stack]   ── CORS → RequestID → structlog binding → GZip → Auth (if guarded)
   ↓
[Router endpoint]    ── parses request via Pydantic schema
   ↓
[Dependencies]       ── get_db, get_current_user, require_admin
   ↓
[Service call]       ── business logic, wraps repo calls in a single transaction
   ↓
[Repository]         ── SQLAlchemy async queries
   ↓
[DB]
   ↑
[Service]            ── shapes domain object
   ↑
[Router]             ── returns Pydantic `*Read` schema
   ↑
[Middleware]         ── adds RequestID header, latency log line
   ↑
Client response
```

---

## 5. Async Strategy

- **All I/O is async**: DB (`asyncpg`), HTTP (`httpx`), Cloudinary uploads (chunked async streaming).
- **Sync-only libraries** (e.g. Razorpay SDK if it stays sync, image transforms via Pillow) are wrapped with `anyio.to_thread.run_sync(...)` — never block the event loop directly.
- **CPU-bound work** (image resizing, CSV parsing of large files) is offloaded to background tasks. At launch this is FastAPI `BackgroundTasks`; once volume justifies it, migrate to Arq workers (§10).

---

## 6. Configuration & Secrets

- One `Settings` class via `pydantic-settings`, loaded once at app boot, injected via `Depends(get_settings)` everywhere it's needed.
- Settings are environment-driven only. Never hard-code values or read `os.environ` in app code.
- Each environment (`dev`, `staging`, `prod`) has its own `.env`. Production secrets live in the hosting provider's secret store (Railway/Render dashboard) — never committed.
- See `.env.example` checklist in [DEPLOYMENT.md](DEPLOYMENT.md) §3.

---

## 7. Database & Transactions

- **One session per request**, managed by `get_db()` dependency. Auto-rollback on exception, auto-commit on success.
- **Service-level transactions:** services that span multiple writes (e.g. `checkout_service.place_order` → create order, decrement stock, create payment) wrap them in `async with session.begin():` to guarantee atomicity.
- **Repository functions are query primitives.** They do not commit; the calling service decides.
- **Read replicas:** not in scope for Phase 1. The architecture allows adding a read-only engine in `db/session.py` and routing GET-heavy services to it without touching repositories — but defer this until needed.
- See [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) for entity definitions.

---

## 8. Error Handling

- One global exception handler (`core/exceptions.py`) that maps domain exceptions to HTTP responses:
  - `NotFoundError` → 404 `{ "detail": "...", "code": "not_found" }`
  - `ConflictError` → 409
  - `ValidationError` → 422 (FastAPI default)
  - `PermissionDeniedError` → 403
  - `PaymentFailedError` → 402
  - Unexpected `Exception` → 500 with `request_id`; full traceback logged, never returned.
- **No try/except in routers.** Routers let domain exceptions propagate; the handler shapes the response.
- Error responses follow a single shape: `{ "detail": str, "code": str, "request_id": str }`.

---

## 9. Logging & Observability

- Structured JSON logs via `structlog`. Every request gets a `request_id` bound to the context — propagated into every log line emitted within that request.
- **Log levels:**
  - `INFO`: request start/end with latency, payment created, order placed, webhook received
  - `WARNING`: invalid token, idempotency replay, retried external call
  - `ERROR`: unhandled exception, external provider failure
- **No PII in logs.** No email addresses, no phone numbers, no full addresses, no payment instrument data.
- **Monitoring** (post-launch): Sentry for errors, Railway/Render built-in metrics for CPU/memory. See [DEPLOYMENT.md](DEPLOYMENT.md) §7.

---

## 10. Background Tasks Strategy

| Use case | Phase 1 mechanism | Phase 2 mechanism |
|---|---|---|
| Send order confirmation email | `BackgroundTasks` after order placement | Queued job |
| Cloudinary upload from admin | Frontend → signed direct upload to Cloudinary; backend just records the URL | unchanged |
| CSV bulk product import | Synchronous for small CSVs (< 200 rows) at launch; otherwise `BackgroundTasks` with a job-status table | Queue job + worker |
| Recently-viewed pruning | DB query at read-time (`ORDER BY viewed_at DESC LIMIT 10`) | unchanged |
| Stock low-alert | Computed on read; flagged on dashboard | Cron-style scheduled job |

If multiple background jobs accumulate (post-launch), introduce **Arq** (lightweight, asyncio-native, Redis-backed) before reaching for Celery. Celery is heavier and stateful; Arq fits the async stack cleanly.

---

## 11. Security Baseline

- Clerk JWT verification on every authenticated endpoint (see [AUTH_FLOW.md](AUTH_FLOW.md)).
- Webhook endpoints verify provider signatures (Razorpay HMAC, Clerk Svix, Meta `X-Hub-Signature-256`). Reject unsigned or invalid-signature requests with 401.
- Rate limiting via `slowapi`: aggressive on webhook and search endpoints, moderate on cart/checkout, permissive on catalog GETs.
- CORS: allowlist only the production frontend domain and the Vercel preview domain pattern. Never `*`.
- All admin endpoints require `role: admin` in Clerk session claims.
- HTTPS-only in production (enforced by Railway/Render edge).
- Pydantic at the boundary — no raw dict access on request payloads.
- SQL injection: SQLAlchemy parameterised queries only. Never f-string interpolate into a query.

---

## 12. Coding Conventions

- **Naming:**
  - Routers: `/api/v1/<resource>` plural, kebab-case in URL, snake_case in Python files.
  - Service methods: verb-first (`create_order`, `decrement_stock`).
  - Repository methods: `get_by_id`, `list_published`, `count_by_status`, etc. Predictable verbs.
- **Imports:** absolute (`from app.services.order_service import ...`), never relative beyond a package's own files.
- **Type hints:** required on every public function/method (mypy strict in CI).
- **Pydantic schemas:** suffix with role — `ProductCreate`, `ProductUpdate`, `ProductRead`. Response models declared explicitly on routes.
- **Tests:** mirror the `app/` tree under `tests/`. Unit tests mock repositories; integration tests use a transactional test DB.

---

## 13. What is Intentionally Excluded (Phase 1)

- GraphQL — REST only.
- gRPC — REST only.
- Event sourcing / CQRS — direct CRUD via repositories.
- Read-replica routing — single primary.
- Microservice split — one FastAPI process.
- Redis cache — not at launch; introduced when a measured hot endpoint demands it.
- Feature flags — none. Cycle scope is the gate.

---

## 14. Open Architecture Questions

| Question | Why it matters | Default if unanswered |
|---|---|---|
| Use a unit-of-work pattern for cross-aggregate writes? | Cleaner service code, but adds an abstraction | No — use `async with session.begin():` directly for now |
| Repository base class via generics? | DRY for simple CRUD | Yes — `BaseRepository[Model]` with `get/list/create/update/delete` |
| Soft delete vs hard delete on Products | PRD §6.1 says "soft-delete to unpublish without losing data" | `status` enum with values `draft / published / unavailable / archived`; never DELETE |
| Order number format | UX + invoice readability | `OOR-YYYYMM-XXXXX` (sequence per month) |
| Idempotency key strategy for payment endpoints | Prevent double-charge on client retry | Required header `Idempotency-Key`; cached in a `request_idempotency` table for 24h |
