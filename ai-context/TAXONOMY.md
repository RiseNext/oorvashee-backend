# TAXONOMY.md — Canonical Merchandising Taxonomy

**Status:** Aligned 2026-05-27 (pre-F2). One source of truth established.

> The **client-approved frontend collection/menu structure is the canonical
> merchandising taxonomy.** The backend + Neon data has been aligned to it.
> There is now ONE taxonomy, not two. This document is the authoritative
> reference for category slugs, hierarchy, naming, and the slug strategy.

---

## 1. The problem we fixed (audit)

Before this alignment there were **two conflicting taxonomies**:

| | Frontend (client-approved, canonical) | Backend / Neon seed (before) |
|---|---|---|
| Naming basis | Region/weave/style departments | Fabric facets |
| Examples | Banaras Sarees, Kanchi Silk, Gadwal Silk, Narayanapet, Mangalgiri, Harini Pattu, Kalamkari, Designer, Fancy, Cocktail, Pure Kanjivaram, Kanchipattu | banarasi-silk, kanchipuram-silk, mysore-silk, tussar-silk, chanderi, patola, georgette, cotton, linen |
| Hierarchy | "Pattu" parent → weave children | flat, all `kind=fabric` |
| Slugs | `banaras-sarees`, `kanchi-silk`, … | `banarasi-silk`, `kanchipuram-silk`, … |

**Concrete mismatches:**
- **Duplicate meaning / wrong slugs:** `banarasi-silk` (backend) ≠ `banaras-sarees` (approved); `kanchipuram-silk` ≠ the three approved Kanjivaram lines (`kanchi-silk`, `pure-kanjivaram-silk`, `kanchi-pattu-saree`).
- **Missing categories:** backend had no Gadwal / Narayanapet / Mangalgiri / Harini / Kalamkari / Designer / Cocktail / Fancy.
- **No hierarchy:** "Pattu" parent grouping didn't exist in data.
- **Hack:** F1 bridged the gap with a frontend-only merchandising map (`lib/catalog/category-map.ts`) translating curated slug → backend slug. That was a temporary seam — now retired.

The frontend `Category` MODEL was already capable (self-referential `parent_id`,
`description`, `seo_*`, `image_url`) — only the **seed data** was wrong.

---

## 2. Canonical taxonomy (source of truth)

The navigable merchandising tree. Stored as `kind=collection` (the model's
documented free-form/"collection" kind — matches the client's "Collection"
nav language). Slugs are the **approved frontend slugs** and are immutable.

```
Pattu  (pattu)                              ← parent department
├── Gadwal Silk Sarees     (gadwal-silk-sarees)
├── Kanchi Silk            (kanchi-silk)
├── Narayanapet Sarees     (narayanapet-sarees)
├── Mangalgiri Sarees      (mangalgiri-sarees)
└── Harini Pattu           (harini-pattu)
Cotton Sarees              (cotton-sarees)
Banaras Sarees             (banaras-sarees)
Designer Sarees            (designer-sarees)
Kalamkari Sarees           (kalamkari-sarees)
Pure Kanjivaram Silk       (pure-kanjivaram-silk)
Fancy Sarees               (fancy-sarees)
Kanchipattu Sarees         (kanchi-pattu-saree)
Cocktail & Party Wear      (cocktail-party-wear-sarees)
```

Plus **seasonal collections** (also `kind=collection`, surfaced via banners,
not the permanent nav): `diwali-2026`, `wedding-edit`.

### Orthogonal facets (kept — complementary, NOT a competing taxonomy)
These are filter axes, not navigable departments. A product belongs to ONE+
department AND carries facets:
- `occasion`: bridal, wedding, festive, daily-wear, office, casual
- `region`: south-indian, north-indian
- `color`: maroon, red, pink, gold, cream, navy, green, blue, mustard, multicolor
- `price_bracket`: under-5k, 5k-15k, 15k-50k, above-50k

The old `kind=fabric` categories were **removed** — fabric lives on the
variant (`variant.fabric`) and the department names already convey weave.

---

## 3. Modeling decisions

| Decision | Rationale |
|---|---|
| Merchandising tree uses **`kind=collection`** (no new enum value) | The model documents `collection` as the free-form kind; it matches the client's "Collection" nav. Avoids an `ALTER TYPE category_kind ADD VALUE` migration — **zero schema migration, launch-safe, testable by reseed alone.** |
| **Hierarchy** via existing `parent_id` | "Pattu" is a real parent row; its 5 weaves set `parent_id = pattu`. Model already supported this. |
| **Parent gets the union** — Pattu-child products are linked to BOTH the child AND `pattu` | So `/saris/pattu` shows all silk-weave products without recursive querying; `/saris/gadwal-silk-sarees` shows just Gadwal. Standard ancestor-tagging denormalisation. |
| Display copy (title/subtitle/SEO) lives on the **category row** (`name`, `description`, `seo_*`) | Single source of truth; frontend reads it, no hardcoded copy. `description` is now exposed on `CategorySummary`. |
| Old fabric categories + old demo products **archived/deactivated** (never deleted) by the merchandising sync | Production DB has order history — deletes are unsafe. See [MERCHANDISING_SYNC.md](MERCHANDISING_SYNC.md). |

### Slug strategy
- Slugs = approved frontend slugs, lowercase-kebab, **immutable** (URL + SEO
  contract). New categories added later get a stable slug at creation and
  never change it (rename `name`, never `slug`).
- Frontend nav (`siteConfig.nav`) links to `/saris/<slug>` where `<slug>` is
  now a **real backend category slug** → navigation resolves to real data
  with no translation layer.

---

## 4. What changed in code

| File | Change |
|---|---|
| `app/seeds/data.py` | `CATEGORIES` rebuilt to the canonical tree (kind=collection + parent_slug + description/seo) + kept facets; `PRODUCTS` re-themed to populate every department with correct department/parent/facet links; `LEGACY_CATEGORY_SLUGS` + `LEGACY_PRODUCT_SLUGS` for migration cleanup |
| `app/seeds/sync.py` + `runner.py` | The category/product/variant/inventory/image/banner reconciliation now lives in the non-destructive `MerchandisingSync`; `run_seeds` delegates to it; `reset_seeds` was **removed**. See [MERCHANDISING_SYNC.md](MERCHANDISING_SYNC.md) |
| `app/schemas/category.py` | `CategorySummary` gains `description` (display copy, single source of truth) |
| frontend `lib/catalog/category-map.ts` | **Retired** — slugs now match; `/saris/[category]` resolves directly from the backend category (name + description). Breadcrumbs/home use real slugs. |

**No Alembic migration** — `parent_id`, `description`, `seo_*` columns already
exist; `kind=collection` is an existing enum value. The change is data + read-shape only.

---

## 5. Neon migration runbook (run by backend owner)

Because the running backend/Neon was not reachable from the implementation
environment, apply + verify these steps against the live DB:

```bash
cd BACKEND
# 1. Deploy the new code (seed data + sync service + schema read change).
#    No `alembic upgrade` needed — there is NO schema migration.
git pull && uv sync

# 2. Apply the canonical taxonomy via the PRODUCTION-SAFE sync (upsert +
#    archive/deactivate; never deletes — order history is preserved).
uv run python -m scripts.sync_catalog sync --dry-run   # preview (rolls back)
uv run python -m scripts.sync_catalog sync --yes       # apply (--yes for prod)
uv run python -m scripts.sync_catalog status           # confirm counts

# 3. Sanity
curl "$API/api/v1/categories" | jq '.collection[].slug'   # canonical slugs
curl "$API/api/v1/products?category=pattu" | jq '.total'   # parent union > 0
curl "$API/api/v1/products?category=banaras-sarees" | jq '.total'
```

**This is now non-destructive and production-safe** — full architecture +
verification (incl. orphan-relation SQL) in [MERCHANDISING_SYNC.md](MERCHANDISING_SYNC.md).
The old fabric categories + pre-alignment demo products are **archived/
deactivated**, not deleted, so any orders against them stay intact.

---

## 6. Verification checklist (post-reseed, live)

- [ ] `/categories` `collection[]` contains the §2 slugs; `fabric[]` is empty.
- [ ] Every department slug returns products (`/products?category=<slug>` > 0); no orphan departments.
- [ ] `/products?category=pattu` returns the union of all five weave children.
- [ ] No legacy slug resolves (`banarasi-silk`, `kanchipuram-silk`, … return empty / 404 at the category page).
- [ ] Each product's `categories[]` carries its department + parent + facets, correct names.
- [ ] Frontend: navbar links, category pages, breadcrumbs, home tabs all resolve to real categories; no `category-map` translation remains.
