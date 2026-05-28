# AUTH_FLOW.md

> **Status:** IMPLEMENTED (Phase 2E complete; hardened 2026-05-28).
> JWT verification (incl. audience) + JWKS cache + auto-provisioning (email-validated, conflict-safe) + Clerk webhook (Svix-signed) + `require_role` factory are all live.
> Source files: [app/core/security.py](../app/core/security.py), [app/core/validators.py](../app/core/validators.py), [app/integrations/clerk.py](../app/integrations/clerk.py), [app/services/user_sync_service.py](../app/services/user_sync_service.py), [app/routers/webhooks/clerk.py](../app/routers/webhooks/clerk.py), [app/schemas/clerk_webhook.py](../app/schemas/clerk_webhook.py).

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

Next.js uses `@clerk/nextjs` for sign-in (email / Google / phone OTP per PRD §5.3). On the client, the frontend mints the JWT from a **named Clerk JWT template** rather than the default session token:

```ts
await getToken({ template: "backend" })   // NEXT_PUBLIC_CLERK_JWT_TEMPLATE (defaults to "backend")
```

It attaches the result as `Authorization: Bearer <jwt>` to every authenticated call (`/account/*`, `/cart/*`, authenticated checkout, `/admin/*`). All authed calls flow through one seam (`hooks/use-api-client.ts` → `authedFetch`); guests send no token.

**The "backend" template is mandatory** — it is what mints the claims the backend verifies:

| Claim | Clerk template shortcode | Backend use |
|---|---|---|
| `aud` | `"oorvashee-api"` (static) | Verified against `CLERK_AUDIENCE` (§3) |
| `email` | `{{user.primary_email_address}}` | Provisioning email (§3.4) |
| `role` | `{{user.public_metadata.role}}` | RBAC (§4) |

> ⚠️ **Do not** use the suffix form `{{user.primary_email_address.email_address}}` — it is **not** a valid Clerk shortcode and renders as a *literal string*, which previously poisoned `users.email` (see §3.4). The default session token (plain `getToken()`) lacks `aud` and is now rejected.

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
   1. Read Authorization header → extract Bearer token (scheme must be Bearer, else 401)
   2. Decode header → read `kid`
   3. Fetch Clerk JWKS (cached in memory, TTL CLERK_JWKS_CACHE_TTL_SECONDS / refresh on `kid` miss)
   4. Verify RS256 signature with matching JWK
   5. Validate claims (PyJWT):
        - require: exp, iat, iss, sub
        - iss == CLERK_ISSUER (trailing slash normalised)
        - aud == CLERK_AUDIENCE  → enforced ONLY when CLERK_AUDIENCE is set
          (verify_aud = bool(CLERK_AUDIENCE)); prod sets it to "oorvashee-api"
        - exp not past, nbf not future
        - azp ∈ CLERK_AUTHORIZED_PARTIES (checked only if the list is configured)
   6. Build Principal: sub → clerk_user_id; email ← `email` claim; role ← `role` claim (default "customer")
   7. Resolve local `user` row by clerk_user_id (get_current_user):
        - Found → return it.
        - Missing → DEFENSIVE auto-provision (webhook is the primary path; this covers lag):
            a. Validate the `email` claim with is_plausible_email — reject blank
               OR template-literal junk (raise 401 `invalid_email_claim`); never persist it.
            b. INSERT inside a SAVEPOINT. On IntegrityError:
                 · concurrent insert of the same clerk user → re-query & return that row;
                 · email already owned by a DIFFERENT identity → 401 `email_conflict` (not a 500).
   8. Return User model
```

> `get_current_principal_optional` runs steps 1–6 only (returns `Principal | None` for guest-or-authed routes, e.g. checkout); `get_current_user` adds step 7 (the local row + provisioning) and is what cart/account/order routes depend on.

### 3.2 Implementation choice

Use **`PyJWT` + JWKS fetch**, not the unofficial `clerk-backend-api` package, because:
- We need explicit control over JWKS caching to survive Railway cold starts.
- The integration surface is tiny (verify + claim extraction).
- Fewer dependencies = fewer supply-chain surprises.

Wrapper module: `app/integrations/clerk.py`.

### 3.3 Dependencies (FastAPI)

```python
get_current_principal_optional  # → Principal | None. Verifies the JWT if present; no DB row. Guest-or-authed routes (e.g. /checkout/orders).
get_current_principal           # → Principal. Raises 401 if no/invalid token.
get_current_user                # → User. Resolves/auto-provisions the local row (steps 7a/7b). Cart, /account/*, orders.
require_admin                   # depends on get_current_principal, raises 403 if role != admin.
require_role("admin", "staff")  # dependency factory — role ∈ allowed set, else 403. Reads the JWT claim (no DB hit).
```

Every router file declares the dependency it needs explicitly. No "global auth" middleware that surprises a developer reading a router file. Role checks read the JWT `role` claim directly; the local `user_roles` table is kept in sync by the webhook for admin-list queries + audit, but is **not** consulted on the hot path.

### 3.4 Email-claim validation & provisioning hardening

`app/core/validators.is_plausible_email()` is the shared guard for any email
that will be written to `users.email`. It rejects blanks, anything containing
template braces (`{{` / `}}`), and anything failing a basic `local@domain.tld`
shape. It is applied in **both** write paths:

- `get_current_user` auto-provisioning (the JWT-claim fallback), and
- `UserSyncService._create` (the webhook path), defensively.

**Why this exists (incident, 2026-05-28):** a misconfigured Clerk JWT template
emitted the unrendered literal `{{user.primary_email_address.email_address}}` as
the `email` claim. The old fallback wrote it verbatim → the first signup
inserted, then every later signup hit `duplicate key value violates unique
constraint "uq_users_email"`, which cascaded into failed cart/wishlist creation
and broken sessions for new users. The validator + the SAVEPOINT/`IntegrityError`
handling (§3.1 step 7) make this class of bug impossible: junk emails are
rejected with a clear 401 instead of being persisted, and a unique collision can
never 500 the request.

Carts and wishlists are **not** created at provisioning time: the cart is created
lazily on first use (`CartRepository.get_or_create_for_user`) and a wishlist is a
set of per-item rows (no container entity). The earlier cart/wishlist failures
were downstream symptoms of the poisoned `users` row, not independent bugs.

Cleanup for any pre-existing poison rows: [scripts/cleanup_corrupted_clerk_users.sql](../scripts/cleanup_corrupted_clerk_users.sql)
(preview-then-delete; cascades via FK; skips any user with orders).

---

## 4. Admin Role Resolution

Two options:

| Option | Mechanism | Trade-off |
|---|---|---|
| A — Clerk public metadata | Set `publicMetadata: { role: "admin" }` on the user in the Clerk dashboard. Backend reads `role` from JWT claims. | Single source of truth (Clerk). No DB sync needed. Easiest. |
| B — Local DB flag | `user.role = 'admin'` enum, set manually via DB or admin tool. | Backend works even if Clerk metadata is wrong. Requires sync. |

**Recommendation: A + B both.** Store `role` in the local `user` table for fast checks and admin-list queries; sync it from Clerk on each webhook event. The runtime authorization decision reads the DB row (which was just authoritatively populated by Clerk). This gives DB query convenience plus Clerk as the source of truth.

---

## 5. User Provisioning via Clerk Webhook (PRIMARY path)

The webhook is the proactive provisioning path; `get_current_user` (§3.1 step 7)
is only a lag fallback. Clerk emits Svix-signed events for `user.created`,
`user.updated`, `user.deleted`. Payload is parsed into the `ClerkUser` schema
([app/schemas/clerk_webhook.py](../app/schemas/clerk_webhook.py)), whose
`primary_email` / `primary_phone` / `full_name` / `role` properties extract from
the real payload — e.g. `email` from `email_addresses[]` (preferring
`primary_email_address_id`, else first). No template placeholders are involved.

| Event | Backend action (`UserSyncService`) |
|---|---|
| `user.created` | INSERT `users` row (`clerk_user_id`, validated `email`, `phone`) + `user_profiles` row (`full_name` from first/last, `avatar_url` from `image_url`) + reconcile `user_roles` to the single `public_metadata.role` (default `customer`; unknown roles auto-created). |
| `user.updated` | UPDATE matching row (email/phone/profile/role); clears `deleted_at` if Clerk revived the user. |
| `user.deleted` | Soft-delete — set `email` to `deleted+<id>@oorvashee.invalid`, blank phone, scrub profile PII, revoke roles. Row kept so `order.user_id` FK survives. |

**Idempotency / replay-safety** (handlers are designed for Svix retries):
- `user.created` for an existing user → falls through to the update path (UNIQUE on `clerk_user_id`; no duplicate row).
- `user.updated` for an unknown user → creates it (handles Clerk emitting update-before-create).
- `user.deleted` re-delivered → no-op if already soft-deleted.
- `email` is validated by `is_plausible_email` before insert (§3.4); a payload without a usable email raises rather than poisoning the table.

Webhook endpoint: `POST /api/v1/webhooks/clerk`. Signature + replay protection via the official Svix library (`app/integrations/clerk.verify_clerk_webhook`); only `user.*` events are handled, others return `{status: ignored}` 200 so Svix doesn't retry.

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
- **Verify `aud`** — set `CLERK_AUDIENCE=oorvashee-api` in production; the backend then requires `aud == "oorvashee-api"` (minted by the Clerk "backend" template), rejecting default-session-token or wrong-audience tokens. Audience verification is skipped only when `CLERK_AUDIENCE` is blank (dev convenience).
- **Verify `azp`** — Clerk's "authorized party" claim should match the frontend origin we expect, defending against stolen tokens used from a different origin.
- **Never persist unvalidated identity claims** — the `email` claim is validated (`is_plausible_email`) before it is written to `users.email`; provisioning is `IntegrityError`-safe (§3.4). Prevents template-literal poisoning + duplicate-key cascades.
- **Webhook signatures** — Svix headers (`svix-id`, `svix-timestamp`, `svix-signature`) are mandatory. Reject older than 5 min to mitigate replay.
- **Admin actions** — every admin write logs `actor_user_id` to the relevant audit table (e.g. `stock_movement.actor_user_id`).
- **No password storage** — Clerk owns auth entirely; backend never sees or stores passwords.

---

## 10. Local Development

- Use Clerk's **development instance** with a separate publishable/secret pair. Different `CLERK_ISSUER` and JWKS URL than production.
- Create a JWT template named **`backend`** in the Clerk dashboard with claims `{ "aud": "oorvashee-api", "email": "{{user.primary_email_address}}", "role": "{{user.public_metadata.role}}" }`. The frontend requests tokens from this template (`NEXT_PUBLIC_CLERK_JWT_TEMPLATE`, default `backend`).
- Set `CLERK_AUDIENCE=oorvashee-api` to mirror prod. Leaving it blank skips the `aud` check locally, but testing with it set catches template mistakes before deploy.
- The dev instance accepts the localhost frontend origin (`http://localhost:3000` in `CLERK_AUTHORIZED_PARTIES`).
- For seeded admins, create the user in Clerk dashboard then set `publicMetadata.role = "admin"` manually. The webhook will sync the local DB row on next event.

---

## 11. Open Auth Questions

| Question | Default if unanswered |
|---|---|
| Should backend issue an "API key" auth scheme for the bot webhook payload (in addition to Meta's signature)? | Yes — a static shared secret in `X-Bot-Token` header, rotated quarterly. Defence in depth on top of Meta signature. |
| Should admin sessions have shorter token lifetimes than customer sessions? | Yes if Clerk allows per-template lifetimes — set admin to 15 min. Otherwise rely on Clerk default. |
| Multi-factor auth for admin? | Phase 2. Clerk supports it; enable in Clerk dashboard when the owner is comfortable. |
| Service-to-service auth (frontend SSR fetching from backend with elevated privileges)? | Not in current scope — the frontend always uses the end-user's JWT or no auth. |
