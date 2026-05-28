# MERCHANDISING_SYNC.md — Production-Safe Catalog Sync

**Status:** Live 2026-05-27. Replaces the destructive seed `reset`/`reseed`.

> The catalog is now a **production relational database with order history.**
> Applying the canonical merchandising (see [TAXONOMY.md](TAXONOMY.md)) must
> never endanger that history. This document describes the safe, idempotent,
> upsert-based sync that does it.

---

## 1. Why this exists (the incident)

The taxonomy-alignment **reseed failed**: `reset_seeds()` issued hard
`DELETE`s on products that were already referenced by `order_items`. The FK
correctly refused (protecting order history), which broke the reseed flow.

**Hard-deleting catalog rows is not acceptable on a production DB.** The fix:
remove destructive reset entirely and reconcile via a non-destructive sync.

---

## 2. Guarantees

The sync (`app/seeds/sync.py::MerchandisingSync`) contains **no `DELETE`,
`TRUNCATE`, or `DROP`** — verified structurally. Therefore:

1. **No product referenced by an order is ever deleted.** (Nothing is deleted.)
2. **Order history integrity is never broken.** `order_items.product_id` /
   `variant_id` always still resolve.
3. **No production table is dropped/truncated.**
4. **Existing orders/order_items remain fully intact.**
5. **Historical product/variant IDs stay stable** — rows are UPSERTed in place
   (SELECT-then-INSERT/UPDATE), never recreated.

---

## 3. How it reconciles

| Entity | Strategy | Key |
|---|---|---|
| Categories | UPSERT (create or refresh name/desc/SEO/order; reactivate if returning) | `slug` |
| Category hierarchy | second pass sets `parent_id` from `parent_slug` | — |
| Products | UPSERT (refresh mutable fields; re-publish if returning to canonical) | `slug` |
| Product↔category links | **additive** (add missing; never prune — links carry no order meaning and an admin may have added some) | `(product, category)` |
| Variants | UPSERT (refresh colour/fabric/default/active) | `sku` |
| Inventory | UPSERT `stock` only; **never touches `reserved`** (live cart/order holds) | `variant_id` |
| Images | best-effort, idempotent — created only when a product has none | — |
| Banners | UPSERT | `(title, placement)` |

### Retirement (the safe replacement for "delete")
Slugs dropped from the canonical set are listed in `RETIRED_PRODUCT_SLUGS` /
`RETIRED_CATEGORY_SLUGS` (`app/seeds/data.py`). The sync:

- **Retired products → `status=archived`** (+ clears featured/bestseller/new).
  Archived rows are **excluded from the catalog list** (`ProductRepository`
  only returns `published`/`unavailable`) but **still resolve by slug** with
  `available=false` — the bot URL contract (PRD §7.2). Row + id + order links
  preserved forever.
- **Retired categories → `is_active=false`.** Hidden from `/categories`
  (`list_active`), but the row and all `product_categories` links from
  historical/archived products remain intact.

**Scope of authority.** The sync only ever touches its own declared sets:
canonical slugs (upsert) + retired slugs (archive/deactivate). Admin-created
products/categories outside these sets are never modified — so the sync and the
admin dashboard coexist safely.

### Idempotency & repeatability
SELECT-then-write means a second run with an unchanged canonical set makes **no
changes** (everything tallies as `skipped`). Re-archiving an archived product or
re-deactivating an inactive category is a no-op. Safe to run on every deploy.

---

## 4. Code map

| File | Role |
|---|---|
| `app/seeds/data.py` | Canonical catalog data + `RETIRED_*` slug lists |
| `app/seeds/sync.py` | `MerchandisingSync` — the dedicated, non-destructive sync service |
| `app/seeds/runner.py` | `run_seeds` (delegates to the sync) + `seed_status` (verification counts). **`reset_seeds` removed.** |
| `scripts/sync_catalog.py` | **Production-capable CLI** — `sync` (`--dry-run`, `--yes` for prod) + `status` |
| `scripts/seed_dev.py` | Dev convenience CLI — `run` (= sync) + `status`; refuses `APP_ENV=prod` |

---

## 5. Runbook

```bash
cd BACKEND

# 1. PREVIEW against the target DB (runs the full sync, then rolls back).
uv run python -m scripts.sync_catalog sync --dry-run

# 2. APPLY.
#    non-prod:
uv run python -m scripts.sync_catalog sync
#    prod (safe, but writes require explicit intent):
uv run python -m scripts.sync_catalog sync --yes

# 3. VERIFY counts.
uv run python -m scripts.sync_catalog status
```

No `alembic upgrade` needed — this is data reconciliation, not schema change.
The sync is safe to re-run any time (idempotent).

---

## 6. Verification checklist (run against the live DB)

- [ ] `status`: `categories_canonical` == 38, `products_canonical` == 14, `products_published` == 14.
- [ ] `status`: `retired_products_present` == `retired_products_archived` (every retired product archived, none deleted); `retired_categories_inactive` == count present.
- [ ] Orders intact: `SELECT count(*) FROM order_items;` unchanged before/after; every `order_items.product_id` still resolves to a `products` row (no orphans).
- [ ] Catalog excludes archived: `/products?category=banaras-sarees` returns canonical products; the archived legacy demo products do NOT appear in any `/products` list.
- [ ] Bot URL still works: `GET /products/<retired-slug>` returns **200** with `available=false` (not 404).
- [ ] Frontend: every navbar/category route resolves; `/saris/[category]` shows canonical products; PDPs render; no broken slugs.
- [ ] Idempotent: run `sync` twice — the second run reports only `skipped` (zero created/updated).

### Orphan-relation check (SQL)
```sql
-- order_items pointing at a missing product/variant (must be 0):
SELECT count(*) FROM order_items oi
  LEFT JOIN products p ON p.id = oi.product_id
  WHERE p.id IS NULL;
-- product_categories pointing at a missing category (must be 0):
SELECT count(*) FROM product_categories pc
  LEFT JOIN categories c ON c.id = pc.category_id
  WHERE c.id IS NULL;
```

---

## 7. Adding / retiring catalog items later (convention)

- **Add** a product/category: add it to `CATEGORIES`/`PRODUCTS` in `data.py`,
  run `sync`. (Or create it via the admin dashboard — the sync won't touch it.)
- **Retire** a seed product/category: move its slug from the canonical list to
  `RETIRED_PRODUCT_SLUGS` / `RETIRED_CATEGORY_SLUGS`, run `sync`. It is archived/
  deactivated, never deleted — order history and indexed URLs are preserved.
- **Never** reintroduce a destructive reset. If unreferenced rows ever need true
  deletion, do it as a separate, explicit, audited admin action — not in the sync.
