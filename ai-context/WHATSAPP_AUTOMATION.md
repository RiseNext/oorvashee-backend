# WHATSAPP_AUTOMATION.md

> **Status:** PLANNED + PARTIALLY UNCONFIRMED.
> PRD §7 states the WhatsApp and Instagram bots are "already built and functional" and live outside this repository. This doc describes the **website-side contract** that the bots depend on, plus the **optional backend webhook** that would close the analytics loop. Items marked **TBD** require sign-off from Dev 3 (Bot Integration).

---

## 1. PRD-Locked Contract (the only mandatory part)

Per PRD §7.1 and §7.2:

| Contract | Owner | Rule |
|---|---|---|
| Product URL format | Backend (slug) + Frontend (route) | `https://oorvashee.com/product/[product-slug]` |
| Slug generation | Backend | Generated at product creation, **immutable** after publish |
| Slug stability | Backend | Editing the product name MUST NOT change the slug |
| Deleted product | Frontend + Backend | Return 200 with `{ available: false }` so the page renders "Product Unavailable" — never 404 |
| Archived status | Backend | Never DELETE rows. Use `product.status = 'archived'`. Slug stays reserved forever. |

This is the **only** website-side requirement the bots depend on per PRD. Everything else in this document is **optional / future**.

---

## 2. Backend Responsibilities to Satisfy the Contract

1. **Slug uniqueness** — `product.slug` is `UNIQUE NOT NULL` (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) §3.4).
2. **Slug immutability** — at the service layer (`product_admin_service.update_product`), `slug` is never in the update set, even if name changes. Enforced by Pydantic `ProductUpdate` schema omitting the field entirely.
3. **Resolver endpoint** — `GET /products/{slug}` (see [API_CONTRACTS.md](API_CONTRACTS.md) §3) returns:
   - `200 { available: true, ... }` for `status in ('published')`
   - `200 { available: false, status: 'archived', slug, name }` for `status = 'archived'` (preserves bot URL)
   - `200 { available: false, status: 'unavailable', ... }` when stock = 0 across all variants
   - `404` only if slug truly never existed (typo / hand-edited URL)
4. **Sitemap** — auto-generated at `/sitemap.xml` (frontend route) but driven by `GET /products?status=published` from backend.

---

## 3. Optional Webhook (TBD with Dev 3)

PRD §6.5 lists "Traffic Source (Future)" as **NICE TO HAVE** — distinguishing organic / WhatsApp / Instagram traffic. Two implementations would deliver this:

### 3.1 Option A — Frontend-only (zero backend work)

The bot sends URLs with UTM parameters: `?utm_source=whatsapp&utm_campaign=<context>`. The frontend records the visit in a client-side analytics tool (Vercel Analytics, Plausible, etc.). Backend stays out of it.

**Pros:** zero backend code, zero new failure mode.
**Cons:** no first-party attribution data; depends on a third-party analytics tool.

### 3.2 Option B — Backend webhook (richer data, more work)

The bot pings the backend on every product-URL redirect. Backend stores a `bot_event` row (see DATABASE_SCHEMA §3.17) and the admin dashboard surfaces traffic-source counts.

```
POST /api/v1/webhooks/{provider}
Headers:
  X-Bot-Token: <static shared secret> (rotate quarterly)
  X-Meta-Signature: <Meta sha256 HMAC, if direct Meta integration>

Body:
{
  "event": "redirect",
  "slug": "kanchipuram-bridal-maroon-001",
  "external_user_ref": "<sha256 of phone+pepper>",   // never raw phone
  "occurred_at": "2026-05-24T10:32:00Z",
  "metadata": { "campaign": "diwali-2026", "post_id": "..." }
}
```

Server actions:
1. Verify `X-Bot-Token` (and provider signature if present). 401 otherwise.
2. Resolve `slug` → `product_id` (null if not found — still record for monitoring).
3. INSERT `bot_event`.
4. Return `200 { received: true }`.

**Pros:** first-party data; powers Phase 2 analytics like cohort conversion and campaign attribution.
**Cons:** new failure mode (webhook down = lost data; mitigated by bot retry).

**Recommendation:** Skip at launch (Option A via UTM is enough). Ship Option B in Cycle 6+ if Owner asks for traffic-source data.

---

## 4. Provider Specifics (when Option B is built)

### 4.1 WhatsApp

Per PRD: "WhatsApp (custom webhook)". This implies the bot uses Meta's WhatsApp Cloud API or a third-party (e.g. AiSensy, WATI) with a custom webhook layer between Meta and the website. **Backend integration depends on which:**

| Provider | Auth scheme | Payload shape |
|---|---|---|
| Meta WhatsApp Cloud API direct | `X-Hub-Signature-256` (HMAC-SHA256 of body with `WHATSAPP_APP_SECRET`) | Meta-defined; verbose |
| Third-party (AiSensy/WATI) | Provider-specific | Provider-defined |
| Custom Node/Python bridge | Static bearer / shared secret | Whatever the bot dev defines |

**Action:** Dev 3 must confirm which one. Until then, Option B is unbuildable.

### 4.2 Instagram

Per PRD: "Instagram" — likely Meta Graph API for Instagram messaging, which uses the same signature scheme as WhatsApp Cloud API. Same uncertainty applies.

---

## 5. Outbound (Backend → Bot) — NOT IN SCOPE

The PRD describes the bot as the entry point and the website as the conversion endpoint. There is **no requirement** for the backend to send messages back to the bot or to WhatsApp users. If a future "WhatsApp order confirmation" feature is requested:

- Order confirmation is already covered by transactional email via Resend (PRD §8.1).
- A WhatsApp confirmation would require WhatsApp Business API onboarding, message templates, and approval — a 2–4 week process. Out of scope for 29 May launch.

---

## 6. Privacy & Data Handling

If Option B is built:
- **Never store raw phone numbers** received from the bot. Hash with a server-side pepper before insert.
- Bot event data is admin-only (`/admin/analytics/traffic-sources`); never exposed to customers.
- Retention: 12 months by default; configurable. A cron job deletes `bot_event` rows older than the limit.
- If the bot includes message text, **do not store it**. Only metadata (slug, source, timestamp, hashed user ref).

---

## 7. Testing

- **Slug stability test** (mandatory, even at launch with no backend webhook): integration test that creates a product, edits its name 10 times, and asserts `slug` is unchanged after each edit.
- **Archived URL test:** create → publish → archive → assert `GET /products/{slug}` returns `200 available=false`.
- **404 vs unavailable test:** assert that a typo'd slug returns 404 but an archived slug returns 200.
- **Webhook (if built):** signature-verification tests with valid, invalid, and replay payloads.

---

## 8. Summary — What to Build Per Cycle

| Cycle | Backend item | Status |
|---|---|---|
| Cycle 1 | Slug generation + immutability + uniqueness | MUST HAVE (per PRD §7) |
| Cycle 1 | `GET /products/{slug}` with `available` flag for archived | MUST HAVE |
| Cycle 1 | Slug-stability integration tests | MUST HAVE |
| Cycle 5 | Sitemap source endpoint | MUST HAVE (SEO) |
| Cycle 6+ | `/webhooks/whatsapp`, `/webhooks/instagram`, `bot_event` table | OPTIONAL (Owner decision) |

---

## 9. Open Questions (Dev 3 must answer)

1. Which platform exactly hosts the WhatsApp bot? (Meta direct / AiSensy / WATI / custom Node service)
2. Which platform hosts the Instagram bot? (Meta Graph API / third-party)
3. Does the existing bot already emit redirect events anywhere? If yes — where, and can we subscribe?
4. Is there a need for the backend to push out-of-stock notifications back to the bot? (Currently assumed no.)
5. Are there hourly/daily rate limits on the bot platforms we should respect when (later) pushing events the other way?
