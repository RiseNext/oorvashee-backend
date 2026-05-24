# DEPLOYMENT.md

> **Status:** PLANNED — nothing is deployed yet. This document defines the target deployment topology, environment management, CI/CD, and operational procedures, derived strictly from PRD §9.

---

## 1. Topology (Phase 1, per PRD §9.2)

```
                          ┌────────────────────┐
                          │   oorvashee.com    │   (registered separately, DNS at Cloudflare/registrar)
                          └────────┬───────────┘
                                   │
                       ┌───────────┴───────────┐
                       │                       │
                       ▼                       ▼
              ┌─────────────────┐     ┌──────────────────┐
              │  Vercel (Next.js)│     │  api.oorvashee.com│
              │  Edge CDN        │     │  (CNAME → Railway)│
              └─────┬────────────┘     └─────────┬────────┘
                    │  fetches                    │
                    │ API JSON                    ▼
                    └─────────────►   ┌────────────────────┐
                                      │  Railway / Render  │
                                      │  FastAPI container │
                                      │  Uvicorn workers   │
                                      └─────────┬──────────┘
                                                │
                       ┌────────────────────────┼────────────────────────┐
                       ▼                        ▼                        ▼
              ┌────────────────┐      ┌──────────────────┐     ┌──────────────────┐
              │  Supabase /    │      │   Cloudinary     │     │  Resend (email)  │
              │  Neon Postgres │      │   (media CDN)    │     │                  │
              └────────────────┘      └──────────────────┘     └──────────────────┘
                       ▲                                                  ▲
                       │                                                  │
                       │       ┌──────────────────────────────────┐       │
                       └──── ► │  Razorpay (webhook → backend)    │ ◄─────┘
                               └──────────────────────────────────┘
```

| Tier | Provider | Tier (Phase 1) |
|---|---|---|
| Frontend | Vercel | Hobby/Pro |
| Backend | Railway **or** Render | Starter/Standard |
| Database | Supabase **or** Neon | Free → Pro as needed |
| Media | Cloudinary | Free → Plus |
| Email | Resend | Free (3k/mo) |
| Payments | Razorpay | — (per-txn fees only) |
| Errors (post-launch) | Sentry | Free / Team |

**Railway vs Render decision** is deferred to Cycle 0; both are acceptable. Railway has slightly smoother Postgres add-ons and cheaper egress; Render has clearer auto-scaling docs. Either is reversible.

**Supabase vs Neon decision** is deferred to Cycle 0; both are acceptable. Supabase gives you a dashboard + storage + auth (we won't use the last two, but the dashboard is nice). Neon gives you serverless Postgres with branching — useful for preview environments.

---

## 2. Environments

| Env | Purpose | Frontend | Backend | DB | Cloudinary | Razorpay |
|---|---|---|---|---|---|---|
| `local` | Developer machine | `localhost:3000` | `localhost:8000` (or Docker Compose) | Local Postgres in Docker | Cloudinary dev folder | Razorpay **test** mode |
| `staging` | QA + bot integration testing | Vercel preview branch | Railway/Render staging service | Separate Supabase/Neon project (or DB branch) | Cloudinary staging folder | Razorpay **test** mode |
| `prod` | Customer traffic | Vercel production | Railway/Render production service | Supabase/Neon production project | Cloudinary prod folder | Razorpay **live** mode |

A staging environment is **required** — PRD §9 didn't mention one explicitly but launching a payments-integrated site directly into prod is unacceptable.

---

## 3. Environment Variables (`.env.example` checklist)

Each environment has its own values. Production secrets live only in the hosting provider's secret store.

```bash
# --- App ---
APP_ENV=prod                       # local | staging | prod
APP_BASE_URL=https://api.oorvashee.com
FRONTEND_BASE_URL=https://oorvashee.com
ALLOWED_ORIGINS=https://oorvashee.com,https://*.vercel.app
LOG_LEVEL=INFO
REQUEST_ID_HEADER=X-Request-ID

# --- Database ---
DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DBNAME?sslmode=require
DATABASE_POOL_SIZE=10
DATABASE_POOL_MAX_OVERFLOW=10

# --- Clerk ---
CLERK_ISSUER=https://clerk.oorvashee.com
CLERK_JWKS_URL=https://clerk.oorvashee.com/.well-known/jwks.json
CLERK_WEBHOOK_SECRET=whsec_xxx     # Svix signing secret

# --- Razorpay ---
RAZORPAY_KEY_ID=rzp_live_xxx       # rzp_test_xxx in non-prod
RAZORPAY_KEY_SECRET=xxx
RAZORPAY_WEBHOOK_SECRET=xxx

# --- Cloudinary ---
CLOUDINARY_CLOUD_NAME=oorvashee
CLOUDINARY_API_KEY=xxx
CLOUDINARY_API_SECRET=xxx
CLOUDINARY_UPLOAD_PRESET=oorvashee_products
CLOUDINARY_UPLOAD_FOLDER=prod/products

# --- Resend ---
RESEND_API_KEY=re_xxx
RESEND_FROM_EMAIL=orders@oorvashee.com

# --- Bot webhook (optional, only if Option B in WHATSAPP_AUTOMATION.md is built) ---
BOT_WEBHOOK_TOKEN=xxx              # X-Bot-Token shared secret
WHATSAPP_APP_SECRET=xxx            # For Meta signature verification
INSTAGRAM_APP_SECRET=xxx

# --- Observability (Phase 2) ---
SENTRY_DSN=                        # Empty disables Sentry
```

`.env.example` lives at the repo root, committed. Real `.env` is gitignored.

---

## 4. Container & Runtime

### 4.1 Dockerfile (planned)

- Base: `python:3.12-slim`
- Multi-stage build: builder installs deps, final stage copies only the venv + app.
- Non-root user (`appuser`).
- `CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:8000", "--access-logfile", "-", "--timeout", "60"]`
- `HEALTHCHECK` hits `/health/live`.

### 4.2 Worker count

- Railway/Render free/starter tier: typically 0.5–1 vCPU. Use `-w 2` (2 workers); each worker is an async event loop.
- Scale up workers as CPU grows: `workers = (2 * cpu_count) + 1` per Gunicorn guidance — but for async workloads, lower is fine.

### 4.3 Resource sizing (initial)

| Resource | Phase 1 | Trigger to scale up |
|---|---|---|
| Backend CPU | 0.5–1 vCPU | Sustained > 70% |
| Backend RAM | 512 MB – 1 GB | OOM or > 80% |
| Postgres | 1 vCPU / 1 GB / 1 GB storage | Slow queries (> 100 ms p95) or storage > 70% |
| Cloudinary bandwidth | Free 25 GB/mo | Approaching limit |

---

## 5. CI/CD

GitHub Actions, two workflows:

### 5.1 `ci.yml` (on every PR + push)

1. Checkout
2. Set up Python 3.12 + cache
3. Install dependencies
4. `ruff check .` (lint)
5. `mypy app/` (type check)
6. `pytest -q` (unit + integration; integration uses a Postgres service container)
7. Build Docker image (no push) to catch Dockerfile errors

### 5.2 `deploy.yml` (on push to `main`)

1. Run `ci.yml` first
2. Build & push Docker image to GHCR
3. Trigger Railway/Render deploy webhook
4. Wait for health check
5. (Optional, post-launch) Run `alembic upgrade head` as a pre-deploy step — see §6

**Branching:** trunk-based. PRs merge to `main`. `main` is always deployable. Hot fixes branch from `main` and merge back.

---

## 6. Database Migrations

- Alembic generates revisions from model changes.
- **Migrations run automatically on deploy** via a release-phase command, OR manually for risky changes:
  - **Auto:** Railway "pre-deploy command" = `alembic upgrade head`. Simple, default.
  - **Manual:** for any migration that drops columns, renames tables, or changes large tables — run manually with a maintenance window.
- **Backward compatibility:** every migration must be safely deployable while the previous app version is still running (expand-then-contract pattern). Phase 1 has no live data, so this is trivial; enforce the habit early.
- **Rollback plan:** every `downgrade()` is implemented. Reverting a deploy = revert the commit + redeploy + `alembic downgrade -1`. Practice this once in staging before launch.

---

## 7. Observability

| Concern | Phase 1 | Phase 2 |
|---|---|---|
| Logs | Railway/Render captured stdout (JSON via structlog) | Ship to BetterStack / Logtail / Loki for retention |
| Errors | Stdout traces | Sentry — wire in Cycle 5 if time permits, else Day-1 post-launch |
| Metrics | Railway/Render built-in CPU/RAM/latency | Grafana Cloud free tier with Prometheus scrape |
| Uptime | UptimeRobot (free) hitting `/health` every 5 min | Same |
| Slow queries | Supabase/Neon dashboard | `pg_stat_statements` + alerts |

`/health` returns 503 if DB is unreachable, so uptime monitors catch both app death and DB death.

---

## 8. Secrets Management

- **Source of truth:** Railway/Render dashboard secret store (per env).
- **Local dev:** `.env` file, gitignored. Distributed via 1Password vault or similar — never via chat or email.
- **Rotation:**
  - Razorpay keys — rotate annually or on compromise; coordinate with Razorpay dashboard.
  - Clerk webhook secret — rotate annually; Clerk allows two active secrets during rotation.
  - Cloudinary API secret — rotate annually.
  - Bot webhook shared secret — rotate quarterly (lower trust surface).
  - JWT signing — entirely Clerk's responsibility; not our concern.
- **Never** commit `.env` or print secrets in logs (already enforced by structlog scrubbing).

---

## 9. Domain & DNS

| Subdomain | Points to | Provider |
|---|---|---|
| `oorvashee.com` (apex) | Vercel | Vercel A/AAAA records |
| `www.oorvashee.com` | Vercel | CNAME |
| `api.oorvashee.com` | Railway/Render | CNAME |
| `clerk.oorvashee.com` (optional Clerk custom domain) | Clerk | CNAME |

TLS: managed automatically by Vercel and Railway/Render (Let's Encrypt). No manual cert renewal.

---

## 10. Webhook URLs to Register Externally

After backend goes live at `https://api.oorvashee.com`:

| Provider | URL to register | Where |
|---|---|---|
| Razorpay | `https://api.oorvashee.com/api/v1/webhooks/razorpay` | Razorpay dashboard → Webhooks |
| Clerk | `https://api.oorvashee.com/api/v1/webhooks/clerk` | Clerk dashboard → Webhooks |
| WhatsApp (if Option B) | `https://api.oorvashee.com/api/v1/webhooks/whatsapp` | Meta / provider dashboard |
| Instagram (if Option B) | `https://api.oorvashee.com/api/v1/webhooks/instagram` | Meta dashboard |

A pre-launch checklist (Cycle 5) verifies all four are registered and test events are received successfully.

---

## 11. Backup & Disaster Recovery

| Asset | Backup | Restore RTO |
|---|---|---|
| Postgres | Daily automatic snapshots (Supabase/Neon free tier covers 7 days). Weekly off-platform `pg_dump` to S3-compatible storage. | < 2 hours for full restore |
| Cloudinary media | Cloudinary keeps originals indefinitely under our account. Periodic manifest export to ensure we can re-upload. | N/A — Cloudinary handles |
| Code | GitHub (3 copies: GitHub, dev machines, CI cache) | < 5 min |
| Secrets | 1Password (or equivalent) shared vault | < 30 min |

---

## 12. Pre-Launch Checklist (Cycle 5)

- [ ] Production domain DNS resolves and is HTTPS
- [ ] `api.oorvashee.com/health` returns 200
- [ ] Clerk production instance created, JWT validated end-to-end
- [ ] Razorpay live keys swapped in; webhook registered; test payment captured
- [ ] Cloudinary prod folder + upload preset set
- [ ] Resend domain verified (SPF/DKIM); test confirmation email lands in inbox (not spam)
- [ ] All env vars set in Railway/Render prod
- [ ] Alembic up to head; seed admin user created
- [ ] Sitemap reachable at `/sitemap.xml`
- [ ] CORS allowlist contains only prod frontend origin
- [ ] Rate limits active
- [ ] UptimeRobot monitor configured
- [ ] (If using) Sentry DSN set, test error captured
- [ ] Rollback procedure rehearsed once

---

## 13. Cost Model (per PRD §9.2)

| Item | Phase 1 est. monthly |
|---|---|
| Vercel | ₹0 – ₹1,700 |
| Railway / Render | ₹420 – ₹1,700 |
| Supabase / Neon | ₹0 – ₹2,000 |
| Cloudinary | ₹0 – ₹1,700 |
| Resend | ₹0 |
| Razorpay | 2% per transaction |
| Domain | ~₹1,000/year |
| **Total fixed** | **₹420 – ₹7,100/mo** |

Costs scale predictably with traffic. The biggest jumps come from Cloudinary bandwidth (saree imagery is heavy) and Supabase/Neon storage as the catalog grows.

---

## 14. Open Deployment Questions

| Question | Default if unanswered |
|---|---|
| Railway vs Render | Choose at Cycle 0; both work. Slight preference for Railway for the managed Postgres add-on if not on Supabase. |
| Supabase vs Neon | Neon if we want preview-env DB branching (useful for staging); Supabase if we want a dashboard for non-engineering eyes. |
| Self-hosted Redis for rate-limiting? | Skip at launch (single-instance in-memory is fine). Add Upstash Redis when scaling beyond one backend instance. |
| Pre-deploy `alembic upgrade head` automated or manual? | Automated for additive migrations; manual gate for destructive. |
| Single-instance backend or N-instance from day one? | Single instance at launch (Railway/Render auto-restarts on crash); add a second once Cloudinary signature signing or rate-limiter state proves it's needed. |
