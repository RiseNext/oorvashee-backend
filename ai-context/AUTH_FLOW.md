# AUTH_FLOW.md

> **Status:** IMPLEMENTED (Phase 2E complete).
> JWT verification + JWKS cache + auto-provisioning + Clerk webhook (Svix-signed) + `require_role` factory are all live.
> Source files: [app/core/security.py](../app/core/security.py), [app/integrations/clerk.py](../app/integrations/clerk.py), [app/services/user_sync_service.py](../app/services/user_sync_service.py), [app/routers/webhooks/clerk.py](../app/routers/webhooks/clerk.py).

---

## 1. Roles

| Role | Source | Identifies | Notes |
|---|---|---|---|
| Guest | No JWT | Anonymous visitor | Can browse, cart-in-browser, checkout, track orders via `order_number + email` |
| Customer | Clerk JWT | Registered user | Server-side cart, wishlist, addresses, order history |
| Admin | Clerk JWT with `role: admin` in public metadata | Owner (single, per PRD §14) | Full admin dashboard access |

PRD §14 explicitly excludes "Multi-admin / staff role management" from Phase 1, so a single admin user is sufficient. The role check still happens per-request — adding more admins later is a metadata change, not a code change.

---

## 2. Frontend Side (for context)

Next.js uses `@clerk/nextjs` for sign-in (email / Google / phone OTP per PRD §5.3). On the client, `useAuth().getToken()` provides a short-lived JWT (default 1 hour) that the frontend attaches as `Authorization: Bearer <jwt>` to every API call hitting the backend.

The backend never sees Clerk's session cookie. Backend only sees the JWT.

---

## 3. Backend JWT Verification

### 3.1 Flow

```
Request arrives
   │
   ▼
[Auth dependency: get_current_user]
   │
   1. Read Authorization header → extract Bearer token (else 401)
   2. Decode header → read `kid`
   3. Fetch Clerk JWKS (cached in memory, refreshed every 10 min or on `kid` miss)
   4. Verify signature with matching JWK
   5. Validate claims:
        - iss == settings.CLERK_ISSUER
        - aud (if configured)
        - exp not past, nbf not future
        - azp matches an allowed frontend origin
   6. Extract `sub` = clerk_user_id
   7. Look up local `user` row by clerk_user_id
        - If missing → create on-the-fly (defensive fallback in case Clerk webhook lagged)
        - Cache the lookup for the request lifetime
   8. Return User model
```

### 3.2 Implementation choice

Use **`PyJWT` + JWKS fetch**, not the unofficial `clerk-backend-api` package, because:
- We need explicit control over JWKS caching to survive Railway cold starts.
- The integration surface is tiny (verify + claim extraction).
- Fewer dependencies = fewer supply-chain surprises.

Wrapper module: `app/integrations/clerk.py`.

### 3.3 Dependencies (FastAPI)

```python
get_current_user_optional  # returns User | None — used on routes that work for both guest and registered (e.g. /checkout/orders)
get_current_user           # raises 401 if no/invalid token
require_admin              # depends on get_current_user, raises 403 if user.role != admin
```

Every router file declares the dependency it needs explicitly. No "global auth" middleware that surprises a developer reading a router file.

---

## 4. Admin Role Resolution

Two options:

| Option | Mechanism | Trade-off |
|---|---|---|
| A — Clerk public metadata | Set `publicMetadata: { role: "admin" }` on the user in the Clerk dashboard. Backend reads `role` from JWT claims. | Single source of truth (Clerk). No DB sync needed. Easiest. |
| B — Local DB flag | `user.role = 'admin'` enum, set manually via DB or admin tool. | Backend works even if Clerk metadata is wrong. Requires sync. |

**Recommendation: A + B both.** Store `role` in the local `user` table for fast checks and admin-list queries; sync it from Clerk on each webhook event. The runtime authorization decision reads the DB row (which was just authoritatively populated by Clerk). This gives DB query convenience plus Clerk as the source of truth.

---

## 5. User Provisioning via Clerk Webhook

Clerk emits webhooks (Svix-signed) for `user.created`, `user.updated`, `user.deleted`.

| Event | Backend action |
|---|---|
| `user.created` | INSERT `user` row with `clerk_user_id`, `email`, `phone`, `full_name`. `role` defaults to `customer`; if Clerk public metadata contains `role=admin`, copy it. |
| `user.updated` | UPDATE matching row. Re-copy `role` from public metadata. |
| `user.deleted` | Soft-mark — set `email` to `deleted+<id>@oorvashee.invalid`, blank phone, but keep the row so existing `order.user_id` FK survives. |

Webhook endpoint: `POST /api/v1/webhooks/clerk`. Signature verification via the official Svix library (`svix` Python package).

---

## 6. Guest Checkout

PRD §5.2 (MUST HAVE) — purchase without an account.

- No JWT required on `POST /checkout/orders`.
- Customer email + phone + full name collected at checkout.
- Order written with `user_id = NULL`. All other fields populated normally.
- Order tracking via `GET /orders/{order_number}?email=...` (rate-limited per IP).
- If a guest later signs up with the same email, we **do not** automatically claim past orders. (Could be added in a Phase 2 "claim my orders" flow.)

---

## 7. Session & Token Lifecycles

| Item | Lifetime |
|---|---|
| Clerk JWT | ~60 min (Clerk default) |
| Clerk refresh | Handled entirely by `@clerk/nextjs`; backend never touches it |
| JWKS cache | 10 min TTL, force-refresh on unknown `kid` |
| Idempotency-Key cache (`request_idempotency` table) | 24 hours |
| Order-tracking rate-limit window | 1 minute |

The backend is **stateless** with respect to user sessions. No login/logout endpoints — sign-in is purely a Clerk frontend concern.

---

## 8. Authorization Rules per Endpoint Group

Summarised here; enforced per route in [API_CONTRACTS.md](API_CONTRACTS.md).

| Endpoint pattern | Required |
|---|---|
| `GET /products*`, `GET /categories`, `GET /search` | None |
| `POST /checkout/*`, `POST /payments/verify` | None (works for both guest and authenticated) |
| `GET /orders/{number}?email=...` | None + email match |
| `/cart/*` | Authenticated |
| `/account/*` | Authenticated; user can only access their own data (enforced by service, not just route param) |
| `/admin/*` | Authenticated + `role=admin` |
| `/webhooks/*` | Signature-verified; no JWT |
| `/health*` | None |

---

## 9. Security Considerations

- **HTTPS-only** — Clerk JWTs are bearer tokens; transport security is non-negotiable. Railway/Render terminate TLS at the edge.
- **CORS allowlist** — only the production frontend origin and `*.vercel.app` preview pattern (locked to the project). Never `*` even on dev.
- **Token in URL is forbidden** — no `?token=` query param fallback. Authorization header only.
- **Don't log JWTs** — strip `Authorization` header from structlog context.
- **Verify `azp`** — Clerk's "authorized party" claim should match the frontend origin we expect, defending against stolen tokens used from a different origin.
- **Webhook signatures** — Svix headers (`svix-id`, `svix-timestamp`, `svix-signature`) are mandatory. Reject older than 5 min to mitigate replay.
- **Admin actions** — every admin write logs `actor_user_id` to the relevant audit table (e.g. `stock_movement.actor_user_id`).
- **No password storage** — Clerk owns auth entirely; backend never sees or stores passwords.

---

## 10. Local Development

- Use Clerk's **development instance** with a separate publishable/secret pair. Different `CLERK_ISSUER` and JWKS URL than production.
- The dev instance accepts the localhost frontend origin (`http://localhost:3000`).
- For seeded admins, create the user in Clerk dashboard then set `publicMetadata.role = "admin"` manually. The webhook will sync the local DB row on next event.

---

## 11. Open Auth Questions

| Question | Default if unanswered |
|---|---|
| Should backend issue an "API key" auth scheme for the bot webhook payload (in addition to Meta's signature)? | Yes — a static shared secret in `X-Bot-Token` header, rotated quarterly. Defence in depth on top of Meta signature. |
| Should admin sessions have shorter token lifetimes than customer sessions? | Yes if Clerk allows per-template lifetimes — set admin to 15 min. Otherwise rely on Clerk default. |
| Multi-factor auth for admin? | Phase 2. Clerk supports it; enable in Clerk dashboard when the owner is comfortable. |
| Service-to-service auth (frontend SSR fetching from backend with elevated privileges)? | Not in current scope — the frontend always uses the end-user's JWT or no auth. |
