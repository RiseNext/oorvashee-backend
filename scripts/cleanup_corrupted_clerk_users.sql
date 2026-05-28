-- ============================================================================
-- Cleanup: corrupted Clerk users (poisoned email column)
-- ============================================================================
--
-- CONTEXT
--   A misconfigured Clerk JWT template once emitted an UNRENDERED literal
--   (e.g. "{{user.primary_email_address.email_address}}") as the `email` claim.
--   The auth auto-provisioning path wrote that literal into `users.email`:
--     - the first such row inserted,
--     - every later signup collided on UNIQUE(users.email)
--       ("duplicate key value violates unique constraint" / uq_users_email),
--       cascading into failed cart/wishlist creation.
--
--   The code is now fixed (app/core/validators.is_plausible_email guards both
--   the auth fallback in app/core/security.py and UserSyncService._create), so
--   no NEW poison rows can be written. This script removes any EXISTING ones.
--
-- WHAT IT TARGETS
--   Users whose email is a template literal ("{{" / "}}") or has no "@".
--   It deliberately does NOT touch soft-delete scrub tokens
--   (deleted+...@oorvashee.invalid — those contain "@" and no braces).
--
-- SAFETY
--   - Guarded to skip any user that has orders (real customers are never
--     deleted; a poison row never reaches checkout, so this is just insurance).
--   - Child rows (carts, cart_items, wishlists, user_profiles, user_roles)
--     are removed automatically via ON DELETE CASCADE on their user_id FKs.
--   - Run the PREVIEW first; only run the DELETE after confirming the count.
--   - Wrapped in a transaction — inspect, then COMMIT or ROLLBACK.
--
-- USAGE
--   psql "$DATABASE_URL" -f scripts/cleanup_corrupted_clerk_users.sql
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) PREVIEW — how many / which rows would be removed (no writes).
-- ---------------------------------------------------------------------------
SELECT id, clerk_user_id, email, created_at
FROM users u
WHERE (u.email LIKE '%{{%' OR u.email LIKE '%}}%' OR u.email NOT LIKE '%@%')
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id)
ORDER BY created_at;

-- ---------------------------------------------------------------------------
-- 2) DELETE — cascades to carts/cart_items/wishlists/user_profiles/user_roles.
--    Review the preview above, then run this block. COMMIT to apply.
-- ---------------------------------------------------------------------------
BEGIN;

DELETE FROM users u
WHERE (u.email LIKE '%{{%' OR u.email LIKE '%}}%' OR u.email NOT LIKE '%@%')
  AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.user_id = u.id);

-- Inspect the reported row count, then:
--   COMMIT;   -- apply
--   ROLLBACK; -- abort if the count looks wrong
COMMIT;
