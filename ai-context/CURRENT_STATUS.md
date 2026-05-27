# CURRENT_STATUS.md

**Last updated:** 2026-05-26 (post Phase 3G — CSV bulk import live; Phase 3 complete)
**Backend cycle:** Cycles 0–2 ✅ · **Phase 3 admin ✅ — 3A products, 3B categories, 3C inventory ops, 3D orders, 3E analytics, 3F customers, 3G CSV import all live.**

> Source of truth for "what is actually shipped." Other docs describe planned shape; this one describes reality. Update at the end of every session.

---

## 0. Taxonomy alignment (2026-05-27)

The category taxonomy was realigned so the **client-approved frontend
merchandising structure is canonical** and the backend/Neon data matches it
(slugs == storefront slugs, "Pattu" parent hierarchy via `parent_id`,
`kind=collection`; old fabric categories dropped; demo products re-themed to
populate every department; `CategorySummary` gained `description`).
**No schema migration** (columns/enum already existed) — apply with
`uv run python -m scripts.seed_dev reseed --yes`.

**Authoritative spec + audit + runbook:** [TAXONOMY.md](TAXONOMY.md).

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

**This session (Phase 3G — CSV bulk import) shipped:**
- **New table `import_jobs`** (migration 0005) — tracks every CSV upload: kind, status (queued/running/completed/failed), rows_total/processed/succeeded/failed, errors JSONB (capped at 1000 entries), filename, dry_run, failure_reason, created_by, started_at, completed_at, request_id. Indexed on `(kind, status)`, `created_at`, `created_by`.
- **Two new enums**: `ImportKind` (product / variant / inventory), `ImportStatus` (queued / running / completed / failed). Registered in `PG_ENUMS` for the migration discipline.
- **[admin_import.py schemas](../app/schemas/admin_import.py)** — three row schemas (`ProductCsvRow` / `VariantCsvRow` / `InventoryCsvRow`) with permissive Excel-quirks parsing (`true/TRUE/yes/1` booleans, `₹` and comma in decimals, pipe-delimited tags), plus job + listing shapes.
- **[admin_import_service.py](../app/services/admin_import_service.py)** — orchestrator + BackgroundTasks entrypoint (`run_import_job`) + three per-kind appliers. Streams CSV via `csv.DictReader` (constant memory after decode), chunks at 100 rows by default, commits per chunk for partial-failure tolerance. Per-row validation errors collected without aborting the batch. Per-chunk IntegrityError rolls back THAT chunk only. Dry-run mode validates + reports without persisting.
- **Inventory CSV applier** reuses the same `InventoryService.lock_inventory_rows` + `recompute_product_availability` path that 3C uses — admin bulk-update goes through the same row-lock that protects checkout from overselling.
- **[admin/imports.py router](../app/routers/admin/imports.py)** — 6 endpoints under `/api/v1/admin/imports`. Uploads return `202 Accepted` with the job_id immediately; status is polled via `GET /admin/imports/{job_id}`. Template downloads for all three kinds at `/admin/imports/templates/{kind}`.
- **Body-size middleware exempts `/api/v1/admin/imports`** — CSV uploads legitimately exceed the 1 MB JSON cap. The service enforces a hard 50 MB ceiling on the upload itself.
- 63 new unit tests (356 total passing): bool/decimal/int/tags parsers (Excel CSV quirks: BOMs, `₹` symbols, comma thousands separators, pipe-delimited tags, `Yes/Y/1/true` booleans), three row schemas (required fields, defaults, coercions, slug pattern enforcement, extras ignored), target-stock math, error-payload shaping (Pydantic `ctx` scrubbing, raw row truncation at 10 fields), CSV BOM handling.
- **Bug fix**: `tests/test_models_registry.py::EXPECTED_TABLES` updated to include `import_jobs`.

**Endpoints (`/api/v1/admin/imports`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/imports` (filters: kind, status, page, page_size) |
| GET    | `/admin/imports/{job_id}` |
| POST   | `/admin/imports/products` (multipart `file`, `?dry_run=true`) |
| POST   | `/admin/imports/variants` |
| POST   | `/admin/imports/inventory` |
| GET    | `/admin/imports/templates/{kind}` (CSV download) |

**Earlier session (Phase 3F — admin customer operations) shipped:**
- **[admin_customer_service.py](../app/services/admin_customer_service.py)** — CRM-style views over the local `users` table. Read-mostly; the only mutation is `add_note` (writes `audit_logs` with `metadata.type=admin_note`). Real customer mutations flow through Clerk webhook in `UserSyncService` — admin never edits users locally. Privacy-aware: soft-deleted users appear with `is_deleted=true` flag + scrubbed email.
- **KPI math in the service**: AOV (divide-by-zero guard + ROUND_HALF_UP at 2dp matching analytics), `days_since_last_order` with tz-defensive coercion + clock-skew clamp to 0, `is_repeat = paid_orders_count >= 2`, `account_age_days` with clock-skew clamp.
- **[customer_repo.py](../app/repositories/customer_repo.py)** — admin list with order-aggregate subquery baked in (orders_count + paid_orders_count + lifetime_value + last_order_at all in one round-trip, no N+1). Search by email OR phone OR profile.full_name via correlated EXISTS. Per-user KPI single-shot query. Segment summary single-shot aggregate.
- **[admin_customer.py schemas](../app/schemas/admin_customer.py)** — list / detail / KPI / order summary / activity / segment-summary shapes. Sort options: newest, oldest, ltv_desc, orders_desc, last_order_desc.
- **Activity timeline** synthesised from 4 sources (sign-up timestamp, orders, role assignments, audit-log admin notes). Sorted DESC by `at`. Matches the order-timeline pattern from 3D.
- **[admin/customers.py router](../app/routers/admin/customers.py)** — 6 endpoints under `/api/v1/admin/customers`, router-level admin gate.
- 27 new unit tests (293 total passing): KPI math (zero-paid AOV, repeat threshold at 2 paid, AOV rounding HALF_UP, naive-datetime coercion, clock-skew clamps for both `days_since_last_order` and `account_age_days`), audit-log → activity event mapping (admin_note passthrough, other types ignored, actor preserved), schema bounds (note min/max length, extras ignored), lazy-load defense in `_order_to_summary`.
- **Verified live against Neon:** list returns 15 customers, summary tile renders (`total=15, new30d=15, deleted=2`), LTV sort works, detail has full KPI tile with correct divide-by-zero handling (`aov=0.00` on no paid orders), activity timeline includes `signed_up` event, admin note added with `type=note_added`, bogus UUID returns clean 404.

**Endpoints (`/api/v1/admin/customers`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/customers` (q, has_orders, sort, include_deleted, page, page_size) |
| GET    | `/admin/customers/summary` |
| GET    | `/admin/customers/{user_id}` |
| GET    | `/admin/customers/{user_id}/orders` |
| GET    | `/admin/customers/{user_id}/activity` |
| POST   | `/admin/customers/{user_id}/notes` |

**Earlier session (Phase 3E — admin analytics) shipped:**
- **[admin_analytics_service.py](../app/services/admin_analytics_service.py)** — overview KPIs (revenue / orders / AOV / units / new customers), revenue+orders+units time-series trends (day/week/month), top products + top categories (by revenue or units), fulfillment KPIs, customer metrics (new / repeat / returning / guest). Time-range validation (since < until, 366-day cap). AOV math with divide-by-zero guard + ROUND_HALF_UP at 2dp.
- **[analytics_repo.py](../app/repositories/analytics_repo.py)** — single-shot aggregate queries with conditional `SUM(CAST(condition AS INTEGER))` rollups. `date_trunc` uses `literal_column` so SELECT and GROUP BY emit the exact same expression (Postgres rejects parameterised unit strings as different groupings — see the SQL bug note below).
- **[admin_analytics.py schemas](../app/schemas/admin_analytics.py)** — `Granularity` enum, `TimeRangeEcho` echoed on every response (chart labels), `TrendBucket`, `TopProductRow` / `TopCategoryRow`, `FulfillmentKPI`, `CustomerMetrics`, `AnalyticsOverview`. Empty-state responses (`zeros, []`) on quiet windows.
- **Dense bucket-fill** at the service layer — Postgres only returns buckets with activity; service synthesises zero-rows so the chart's x-axis is dense. Postgres `date_trunc('week', …)` returns Monday — `_trunc_date` matches that convention exactly so client + DB agree.
- **[admin/analytics.py router](../app/routers/admin/analytics.py)** — 6 endpoints under `/api/v1/admin/analytics`, router-level admin gate, `Cache-Control: max-age=60, private` on every response (Redis-ready abstraction surface for future).
- 33 new unit tests (278 total passing): range validation matrix (valid + reversed + at-cap + over-cap), top-N bounds, sort-by whitelist, AOV (divide-by-zero, negative-orders defensive, ROUND_HALF_UP at 2dp), bucket truncation (day/week=Monday/month), bucket advance (incl. year rollover), bucket range (one-week dense / Q1 monthly / Monday-aligned weekly / same-day single-bucket / safety brake), default-range helper.
- **Bug fix:** `date_trunc(<unit>, col)` in SQLAlchemy was emitting two different parameter placeholders for the same `unit` string in SELECT and GROUP BY — Postgres treated them as different expressions and rejected the GROUP BY. Replaced with `literal_column(f"'{unit}'")` (unit is a fixed allowlist value, no injection surface).
- **Verified live against Neon end-to-end:** all 6 endpoints return 200 with correct shapes, range validation 422s as expected, trend returns 31 dense buckets, AOV=₹5998 on the single paid order.

**Endpoints (`/api/v1/admin/analytics`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/analytics/overview` (since, until) |
| GET    | `/admin/analytics/trend` (since, until, granularity=day\|week\|month) |
| GET    | `/admin/analytics/top-products` (since, until, limit, sort_by=revenue\|units) |
| GET    | `/admin/analytics/top-categories` (since, until, limit, sort_by) |
| GET    | `/admin/analytics/fulfillment` (no range — "right now" counters) |
| GET    | `/admin/analytics/customers` (since, until) |

**Earlier session (Phase 3D — admin order operations) shipped:**
- **[admin_order_service.py](../app/services/admin_order_service.py)** — admin-facing order lifecycle. State machine: `placed → packed → shipped → delivered` with `cancelled` as a terminal alt-state from any non-delivered status. Each transition runs its own pre-checks: `mark_packed` gates on payment_status, `mark_shipped` requires courier_name + tracking_id, `mark_delivered` auto-flips COD orders to PAID, `cancel_order` restocks inventory via `InventoryService.restock` + recomputes product availability + writes `requires_refund=true` audit metadata when the cancelled order was PAID. Idempotent (re-clicking a button returns the existing order state).
- **`build_timeline()`** — synthesises events from three sources (order timestamp columns + payments table + audit_logs with metadata.type filter). Status-change audit rows are deliberately skipped to avoid duplicating canonical timestamp-column events. Sorted DESC by `at`.
- **[admin_order.py schemas](../app/schemas/admin_order.py)** — list / detail / timeline / shipment / cancel / note / summary schemas. Note bodies separated from `order.notes` (which holds customer-facing instructions like "gift wrap") so the audit timeline doesn't pollute the customer field.
- **[order_repo.py](../app/repositories/order_repo.py)** extensions — `admin_list_query` (filters: q, status[], payment_status[], payment_method, date range, has_shipment via correlated EXISTS, sort), `admin_list_with_aggregates` (item_count + has_shipment map in one round-trip), `status_summary` (single GROUP-less query with `SUM(CAST(condition AS INTEGER))` rollups for all the dashboard tiles), `get_admin` (full eager load).
- **[admin/orders.py router](../app/routers/admin/orders.py)** — 10 endpoints under `/api/v1/admin/orders`, router-level admin gate.
- **`InventoryService.recompute_product_availability` now invoked on cancel** — restocking an order may flip its parent products back from UNAVAILABLE to PUBLISHED. Auto-flip writes its own `STATUS_CHANGED` audit row.
- **Bug fix in [public/orders.py](../app/routers/public/orders.py)** — slowapi's `@profiles.tracking()` decorator needs both `request: Request` AND `response: Response` parameters in the route signature (response is where slowapi injects `X-RateLimit-*` headers). Was missing — `GET /orders/{number}` was 500-ing on every tracking call. Added.
- 35 new unit tests (242 total passing): full status transition matrix (valid + invalid + terminal moves), allowed-targets introspection consistency check (matrix ↔ assert agreement), schema guards (cancel reason / note length / shipment field caps), audit-log → timeline event mapping (admin_note, shipment_updated, cod_paid).
- **Verified live against Neon end-to-end:** create COD product → public checkout → admin skip-shipping rejected 409 → mark_packed (200) → re-mark_packed idempotent (200) → mark_shipped without courier rejected 422 → mark_shipped with metadata (200) → mark_delivered → **payment_status auto-flipped cod_pending → paid** → cancel-after-delivered rejected 409 → admin note added (timeline event type=note_added).

**Endpoints (`/api/v1/admin/orders`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/orders` (filters: q, status[], payment_status[], payment_method, since, until, has_shipment, sort) |
| GET    | `/admin/orders/summary` |
| GET    | `/admin/orders/{id}` |
| POST   | `/admin/orders/{id}/packed` |
| POST   | `/admin/orders/{id}/shipped` |
| POST   | `/admin/orders/{id}/delivered` |
| POST   | `/admin/orders/{id}/cancel` |
| POST   | `/admin/orders/{id}/cod-paid` |
| PUT    | `/admin/orders/{id}/shipment` |
| POST   | `/admin/orders/{id}/notes` |

**Earlier session (Phase 3C — admin inventory operations) shipped:**
- **[admin_inventory_service.py](../app/services/admin_inventory_service.py)** — admin-facing stock adjust (INCREMENT / DECREMENT / SET), threshold update, list, detail, movement timeline, health summary. Reuses `InventoryService.lock_inventory_rows` so admin adjustments share the same row-level `SELECT ... FOR UPDATE` lock that protects checkout from overselling. Reservation-aware: refuses to push stock below `reserved`. Reason whitelist (`MANUAL_ADJUSTMENT`, `RESTOCK`) enforced at both schema + service layers (defense in depth).
- **[InventoryService extensions](../app/services/inventory_service.py)** — promoted `_lock_inventory_rows` → public `lock_inventory_rows` (kept the underscore alias for backwards compat); new `recompute_product_availability(product_id)` that auto-flips PUBLISHED ↔ UNAVAILABLE based on total active stock and writes a `STATUS_CHANGED` audit row with `trigger=inventory_recompute` metadata. Touches DRAFT / ARCHIVED never.
- **[inventory_repo.py](../app/repositories/inventory_repo.py)** — admin list with eager `selectinload(variant.product)` (no N+1), movement timeline with reason/actor/order/date-range filters, health aggregates in a single round-trip GROUP-less query (counts + sums + CAST-to-int conditional rollups for low-stock / out-of-stock).
- **[admin_inventory.py schemas](../app/schemas/admin_inventory.py)** — `StockAdjustmentRequest` with `mode ↔ value` coupling validator, reason whitelist enforced at schema layer, threshold bounds, lean response shapes.
- **[admin/inventory.py router](../app/routers/admin/inventory.py)** — 7 endpoints under `/api/v1/admin/inventory`, router-level admin gate, request_id propagation.
- 22 new unit tests (207 total passing): target-stock math, schema coupling (`SET 0` allowed, `INCREMENT 0` rejected), reason whitelist parity between schema + service, threshold bounds, note length, extras ignored.
- **Bug fix**: global `RequestValidationError` handler sanitises Pydantic's `ctx.error` (raw Python exception object) before serialisation — `JSONResponse` uses stdlib `json.dumps` not `jsonable_encoder`, so the un-serialisable ValueError tripped a 500 on every validator-raised 422 until now. Added `_safe_validation_errors` helper in [core/exceptions.py](../app/core/exceptions.py).
- **Verified live against Neon:**
  - SET 0 on the only variant → product auto-flipped to UNAVAILABLE (audit log captured trigger).
  - SET 7 after the flip → product auto-flipped back to PUBLISHED.
  - DECREMENT 99 on stock=7 → 409 `adjustment_negative_stock`.
  - System reason in adjust payload → 422 `validation_error` (schema layer).
  - Conflicting `low_stock_only=true&out_of_stock_only=true` → 422 `conflicting_filters`.
  - Health endpoint returns `{active=30, units=241, reserved=0, low=2, out=0, healthy=28}` from the seeded catalog.

**Endpoints (`/api/v1/admin/inventory`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/inventory` (filters: q, low_stock_only, out_of_stock_only, page, page_size) |
| GET    | `/admin/inventory/health` |
| GET    | `/admin/inventory/movements` (filters: variant_id, reason[], actor_user_id, order_id, since, until) |
| GET    | `/admin/inventory/{variant_id}` (detail + last 20 movements) |
| GET    | `/admin/inventory/{variant_id}/movements` (per-variant audit timeline) |
| POST   | `/admin/inventory/{variant_id}/adjust` (row-locked, reservation-aware, audited, auto-flips product status) |
| PATCH  | `/admin/inventory/{variant_id}/threshold` |

**Earlier session (Phase 3B — admin category management) shipped:**
- **[admin_category_service.py](../app/services/admin_category_service.py)** — single mutation seam for categories. Slug + `kind` immutable after create (drive SEO URLs + filter-group axis). Image-pair invariant enforced both at schema (Create) and service (Update merged-state). Parent-hierarchy guards: self-parent rejected, descendant-cycle rejected via subtree walk, depth cap `MAX_DEPTH=3`. Image swap triggers best-effort Cloudinary destroy on the OLD `public_id`. Async-safe via explicit `session.refresh()` before Pydantic serialisation (avoids `MissingGreenlet`).
- **[admin_category.py](../app/schemas/admin_category.py)** — Create / Update / Read / List / Reorder schemas. Mass-assignment guards: Update does NOT declare `slug`, `kind`, or `is_active` (visibility flips go through dedicated archive endpoints).
- **[category_repo.py](../app/repositories/category_repo.py)** extensions — `admin_list_query` (q + kinds[] + is_active + parent_id filters), `admin_product_counts` (per-category total + published product counts in one round-trip, no N+1), `descendant_ids` (subtree walk for cycle check), `get_depth` (root-distance for depth cap), `slug_exists` (clean 409).
- **[admin/categories.py router](../app/routers/admin/categories.py)** — 7 endpoints under `/api/v1/admin/categories`, router-level admin gate, request_id propagation.
- 22 new unit tests (185 total passing): slug rules, image-pair validation (both required / both null / mixed rejected), Update schema mass-assignment guards (slug + kind + is_active stripped), slug pattern enforcement, parent UUID validation, reorder bounds, `MAX_DEPTH` sanity.
- **Verified live against Neon:** create root+child+grandchild → 4th-level rejected 422 `hierarchy_depth_exceeded` → PATCH with mass-assignment fields silently dropped → cycle parent rejected 422 → self-parent rejected 422 → archive → re-archive rejected 422 `status_unchanged` → unarchive → reorder → filtered list.

**Endpoints (`/api/v1/admin/categories`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/categories` (filters: q, kind[], is_active, parent_id, page, page_size) |
| POST   | `/admin/categories` |
| GET    | `/admin/categories/{id}` |
| PATCH  | `/admin/categories/{id}` (slug + kind + is_active NOT accepted) |
| POST   | `/admin/categories/{id}/archive` |
| POST   | `/admin/categories/{id}/unarchive` |
| PUT    | `/admin/categories/{id}/order` |

**Earlier session (Phase 3A — admin product CRUD) shipped:**
- **[admin_product_service.py](../app/services/admin_product_service.py)** — single owner of admin product mutations. Slug immutability + uniqueness (with 100-attempt collision resolver), status state machine (DRAFT ↔ PUBLISHED, ARCHIVED, with ARCHIVED→PUBLISHED forced via DRAFT), variant + inventory creation in one transaction, full audit logging on every mutation, no PII in metadata.
- **[admin_product.py](../app/schemas/admin_product.py)** — admin schemas with mass-assignment guards (slug + status NOT in Update body), tag normalisation, variant + image read shapes.
- **[product_repo.py](../app/repositories/product_repo.py)** extensions — `admin_list_query` (status + q + category + featured), `admin_aggregate_stock` (one-shot total-stock + variant-count map, N+1 prevention), `get_admin` (eager loads variants + inventory + images + category_links).
- **[admin/products.py router](../app/routers/admin/products.py)** — 12 endpoints under `/api/v1/admin/products`. Router-level `require_role("admin")` gate; every mutation attributes via `created_by`/`updated_by` (`AuditableMixin`) + writes one `audit_logs` row.
- 27 new unit tests (164 total): slug-generation rules (whitespace, casing, special chars, 220-char cap), full status-transition matrix, SKU duplicate detection, schema-level tag normalisation, mass-assignment protection (slug + status stripped from Update payloads).
- **Verified live against Neon:** create → list → PATCH (with status-in-body silently dropped) → publish → archive → direct publish blocked 422 → unarchive → publish → archive cleanup. End-to-end lifecycle works exactly as designed.

**Endpoints (`/api/v1/admin/products`, all admin-gated):**
| Method | Path |
|---|---|
| GET    | `/admin/products` (filters: q, status[], category_slug, featured_only, page, page_size) |
| POST   | `/admin/products` |
| GET    | `/admin/products/{id}` |
| PATCH  | `/admin/products/{id}` (slug + status intentionally not accepted) |
| POST   | `/admin/products/{id}/publish` |
| POST   | `/admin/products/{id}/unpublish` |
| POST   | `/admin/products/{id}/archive` |
| POST   | `/admin/products/{id}/unarchive` |
| PUT    | `/admin/products/{id}/categories` (replace-semantics) |
| POST   | `/admin/products/{id}/variants` |
| PATCH  | `/admin/products/{id}/variants/{vid}` |
| DELETE | `/admin/products/{id}/variants/{vid}` (soft-deactivate) |
| POST   | `/admin/products/{id}/images` (existing, Phase 2F) |
| DELETE | `/admin/products/{id}/images/{image_id}` (existing, Phase 2F) |

**Earlier session (Phase 2G — production hardening) shipped:**
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
