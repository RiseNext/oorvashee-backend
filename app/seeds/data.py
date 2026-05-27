"""Declarative seed data — categories, products, banners.

The shape is intentionally plain dicts so non-engineers can edit it.
The loaders in `runner.py` resolve cross-references (e.g. product → category
slug → category id, and category `parent_slug` → parent id) at apply time.

CANONICAL TAXONOMY: the category tree below mirrors the client-approved
frontend merchandising structure exactly (see ai-context/TAXONOMY.md). Slugs
match the storefront nav slugs, so the frontend resolves categories directly —
there is no translation layer. Navigable departments are `kind=collection`
with a `parent_slug` hierarchy ("Pattu" → weave children). Facet axes
(occasion/region/color/price_bracket) are kept as complementary filters.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.models.enums import (
    BannerPlacement,
    CategoryKind,
    ProductStatus,
)

# ---------------------------------------------------------------------------
# Categories — canonical merchandising tree + orthogonal facet axes.
# ---------------------------------------------------------------------------
# `parent_slug` (optional) nests a department under a parent department.
# `description` is the storefront subtitle (single source of truth, exposed
# on CategorySummary). `kind=collection` = the navigable department tree.

CATEGORIES: list[dict[str, Any]] = [
    # --- Departments: Pattu (parent) + weave children -----------------------
    {"slug": "pattu", "name": "Pattu", "kind": CategoryKind.COLLECTION, "display_order": 1,
     "description": "Pure silk weaves — heirloom drapes for the occasions that matter.",
     "seo_title": "Pattu Silk Sarees", "seo_description": "Handwoven pure-silk pattu sarees — Gadwal, Kanchi, Narayanpet and more."},
    {"slug": "gadwal-silk-sarees", "name": "Gadwal Silk Sarees", "kind": CategoryKind.COLLECTION, "parent_slug": "pattu", "display_order": 1,
     "description": "Cotton body, silk pallu — the signature interlocked Gadwal weave."},
    {"slug": "kanchi-silk", "name": "Kanchi Silk", "kind": CategoryKind.COLLECTION, "parent_slug": "pattu", "display_order": 2,
     "description": "Lustrous Kanchipuram silk in classic temple borders."},
    {"slug": "narayanapet-sarees", "name": "Narayanapet Sarees", "kind": CategoryKind.COLLECTION, "parent_slug": "pattu", "display_order": 3,
     "description": "Handloom silk-cotton with a quiet, everyday elegance."},
    {"slug": "mangalgiri-sarees", "name": "Mangalgiri Sarees", "kind": CategoryKind.COLLECTION, "parent_slug": "pattu", "display_order": 4,
     "description": "Crisp handloom cotton with fine zari borders."},
    {"slug": "harini-pattu", "name": "Harini Pattu", "kind": CategoryKind.COLLECTION, "parent_slug": "pattu", "display_order": 5,
     "description": "Soft silk drapes, light enough for all-day wear."},

    # --- Departments: top-level ---------------------------------------------
    {"slug": "cotton-sarees", "name": "Cotton Sarees", "kind": CategoryKind.COLLECTION, "display_order": 2,
     "description": "Breathable handloom cotton for daily grace."},
    {"slug": "banaras-sarees", "name": "Banaras Sarees", "kind": CategoryKind.COLLECTION, "display_order": 3,
     "description": "Timeless Banarasi silk in intricate zari and brocade weaves.",
     "seo_title": "Banaras Sarees", "seo_description": "Pure Banarasi katan and tissue silk sarees with Mughal-era zari work."},
    {"slug": "designer-sarees", "name": "Designer Sarees", "kind": CategoryKind.COLLECTION, "display_order": 4,
     "description": "Contemporary statement pieces, thoughtfully composed."},
    {"slug": "kalamkari-sarees", "name": "Kalamkari Sarees", "kind": CategoryKind.COLLECTION, "display_order": 5,
     "description": "Hand-painted and block-printed narrative motifs."},
    {"slug": "pure-kanjivaram-silk", "name": "Pure Kanjivaram Silk", "kind": CategoryKind.COLLECTION, "display_order": 6,
     "description": "Heirloom-grade mulberry silk with tested zari.",
     "seo_title": "Pure Kanjivaram Silk Sarees", "seo_description": "Bridal-grade pure Kanjivaram silk sarees woven on traditional looms."},
    {"slug": "fancy-sarees", "name": "Fancy Sarees", "kind": CategoryKind.COLLECTION, "display_order": 7,
     "description": "Light, flowing drapes for easy festive dressing."},
    {"slug": "kanchi-pattu-saree", "name": "Kanchipattu Sarees", "kind": CategoryKind.COLLECTION, "display_order": 8,
     "description": "The classic South Indian bridal silk."},
    {"slug": "cocktail-party-wear-sarees", "name": "Cocktail & Party Wear", "kind": CategoryKind.COLLECTION, "display_order": 9,
     "description": "Festive-ready sarees for evenings that sparkle."},

    # --- Seasonal collections (banners, not the permanent nav) --------------
    {"slug": "diwali-2026", "name": "Diwali 2026", "kind": CategoryKind.COLLECTION, "display_order": 20,
     "description": "Festive sarees crafted on traditional looms across India."},
    {"slug": "wedding-edit", "name": "Wedding Edit", "kind": CategoryKind.COLLECTION, "display_order": 21,
     "description": "Bridal Kanjivarams, Banarasis, and heirloom silks."},

    # --- Occasion (facet) ---------------------------------------------------
    {"slug": "bridal",      "name": "Bridal",      "kind": CategoryKind.OCCASION, "display_order": 1},
    {"slug": "wedding",     "name": "Wedding",     "kind": CategoryKind.OCCASION, "display_order": 2},
    {"slug": "festive",     "name": "Festive",     "kind": CategoryKind.OCCASION, "display_order": 3},
    {"slug": "daily-wear",  "name": "Daily Wear",  "kind": CategoryKind.OCCASION, "display_order": 4},
    {"slug": "office",      "name": "Office",      "kind": CategoryKind.OCCASION, "display_order": 5},
    {"slug": "casual",      "name": "Casual",      "kind": CategoryKind.OCCASION, "display_order": 6},

    # --- Region (facet) -----------------------------------------------------
    {"slug": "south-indian", "name": "South Indian", "kind": CategoryKind.REGION, "display_order": 1},
    {"slug": "north-indian", "name": "North Indian", "kind": CategoryKind.REGION, "display_order": 2},

    # --- Colour (facet) -----------------------------------------------------
    {"slug": "maroon",       "name": "Maroon",       "kind": CategoryKind.COLOR, "display_order": 1},
    {"slug": "red",          "name": "Red",          "kind": CategoryKind.COLOR, "display_order": 2},
    {"slug": "pink",         "name": "Pink",         "kind": CategoryKind.COLOR, "display_order": 3},
    {"slug": "gold",         "name": "Gold",         "kind": CategoryKind.COLOR, "display_order": 4},
    {"slug": "cream",        "name": "Cream",        "kind": CategoryKind.COLOR, "display_order": 5},
    {"slug": "navy",         "name": "Navy",         "kind": CategoryKind.COLOR, "display_order": 6},
    {"slug": "green",        "name": "Green",        "kind": CategoryKind.COLOR, "display_order": 7},
    {"slug": "blue",         "name": "Blue",         "kind": CategoryKind.COLOR, "display_order": 8},
    {"slug": "mustard",      "name": "Mustard",      "kind": CategoryKind.COLOR, "display_order": 9},
    {"slug": "multicolor",   "name": "Multicolor",   "kind": CategoryKind.COLOR, "display_order": 10},

    # --- Price bracket (facet) ---------------------------------------------
    {"slug": "under-5k",   "name": "Under ₹5,000",      "kind": CategoryKind.PRICE_BRACKET, "display_order": 1},
    {"slug": "5k-15k",     "name": "₹5,000 – ₹15,000",  "kind": CategoryKind.PRICE_BRACKET, "display_order": 2},
    {"slug": "15k-50k",    "name": "₹15,000 – ₹50,000", "kind": CategoryKind.PRICE_BRACKET, "display_order": 3},
    {"slug": "above-50k",  "name": "Above ₹50,000",     "kind": CategoryKind.PRICE_BRACKET, "display_order": 4},
]


# Legacy slugs to delete on reseed so the old (pre-alignment) taxonomy + demo
# catalog don't linger in Neon as orphans / duplicate meaning.
LEGACY_CATEGORY_SLUGS: list[str] = [
    "kanchipuram-silk", "banarasi-silk", "mysore-silk", "tussar-silk",
    "chanderi", "patola", "georgette", "cotton", "linen",
]
LEGACY_PRODUCT_SLUGS: list[str] = [
    "kanchipuram-bridal-maroon", "banarasi-gold-tissue", "mysore-soft-silk-pink",
    "tussar-cream-daily", "cotton-office-navy", "banarasi-wedding-red",
    "chanderi-pastel-green", "georgette-floral-multicolor",
    "patola-double-ikat-royal", "linen-half-half-mustard",
]


# ---------------------------------------------------------------------------
# Products — demo catalog themed to populate every approved department.
# Each product is linked to its department (+ "pattu" parent for weave
# children) and orthogonal facets. Pattu children carry "pattu" so the parent
# page shows the union.
# ---------------------------------------------------------------------------

PRODUCTS: list[dict[str, Any]] = [
    # --- Pattu › Gadwal -----------------------------------------------------
    {
        "slug": "gadwal-pure-silk-peacock-blue",
        "name": "Gadwal Pure Silk Saree — Peacock Blue",
        "short_description": "Cotton-silk Gadwal with a contrast pure-silk pallu and interlocked kuttu border.",
        "description": (
            "A handwoven Gadwal from the looms of Jogulamba Gadwal, Telangana. The "
            "lightweight cotton-silk body carries a rich peacock-blue pure-silk pallu "
            "joined by the signature interlocked (kuttu) technique, finished with a "
            "fine gold-zari temple border."
        ),
        "base_price": Decimal("16999"), "mrp": Decimal("19999"),
        "tags": ["gadwal", "silk", "handloom", "festive", "south-indian", "zari"],
        "featured": True, "is_bestseller": False, "is_new": True,
        "categories": ["gadwal-silk-sarees", "pattu", "festive", "south-indian", "blue", "15k-50k"],
        "variants": [
            {"sku": "OOR-GDW-PBL-01", "color": "Peacock Blue", "fabric": "Gadwal Silk Cotton", "is_default": True,  "stock": 6},
            {"sku": "OOR-GDW-GRN-01", "color": "Bottle Green", "fabric": "Gadwal Silk Cotton", "is_default": False, "stock": 4},
        ],
        "image_count": 3,
    },
    # --- Pattu › Kanchi Silk ------------------------------------------------
    {
        "slug": "kanchi-silk-teal-gold",
        "name": "Kanchi Silk Saree — Teal & Gold",
        "short_description": "Lightweight Kanchipuram silk with a broad gold-zari border, easy to drape.",
        "description": (
            "A daytime-friendly Kanchi silk in deep teal with a broad traditional "
            "gold-zari border and a contrast mustard pallu. Lighter than a bridal "
            "Kanjivaram, it is made for festive lunches, pujas, and family functions."
        ),
        "base_price": Decimal("11500"), "mrp": None,
        "tags": ["kanchi", "kanchipuram", "silk", "festive", "south-indian"],
        "featured": False, "is_bestseller": True, "is_new": False,
        "categories": ["kanchi-silk", "pattu", "festive", "south-indian", "green", "5k-15k"],
        "variants": [
            {"sku": "OOR-KCS-TEL-01", "color": "Teal",    "fabric": "Kanchipuram Silk", "is_default": True,  "stock": 8},
            {"sku": "OOR-KCS-MAR-01", "color": "Maroon",  "fabric": "Kanchipuram Silk", "is_default": False, "stock": 5},
            {"sku": "OOR-KCS-MUS-01", "color": "Mustard", "fabric": "Kanchipuram Silk", "is_default": False, "stock": 6},
        ],
        "image_count": 3,
    },
    # --- Pattu › Narayanapet ------------------------------------------------
    {
        "slug": "narayanapet-silk-cotton-maroon",
        "name": "Narayanpet Silk-Cotton Saree — Maroon",
        "short_description": "Handloom Narayanpet with a temple-checked body and zari-striped pallu.",
        "description": (
            "Woven in Narayanpet on the Telangana–Karnataka border, this silk-cotton "
            "saree carries the characteristic small temple checks across a maroon body "
            "with a contrast zari-striped pallu. Crisp, durable, and made for repeat wear."
        ),
        "base_price": Decimal("3499"), "mrp": None,
        "tags": ["narayanpet", "silk-cotton", "handloom", "daily", "south-indian"],
        "featured": False, "is_bestseller": False, "is_new": False,
        "categories": ["narayanapet-sarees", "pattu", "daily-wear", "south-indian", "maroon", "under-5k"],
        "variants": [
            {"sku": "OOR-NRP-MAR-01", "color": "Maroon", "fabric": "Narayanpet Silk Cotton", "is_default": True,  "stock": 12},
            {"sku": "OOR-NRP-IND-01", "color": "Indigo", "fabric": "Narayanpet Silk Cotton", "is_default": False, "stock": 9},
        ],
        "image_count": 2,
    },
    # --- Pattu › Mangalgiri -------------------------------------------------
    {
        "slug": "mangalgiri-cotton-mustard",
        "name": "Mangalgiri Handloom Cotton Saree — Mustard",
        "short_description": "Crisp Mangalgiri cotton with the signature Nizam zari border.",
        "description": (
            "Pure handloom cotton from Mangalgiri, Andhra Pradesh, in a warm mustard "
            "with the town's signature fine Nizam gold-zari border. Breathable and "
            "office-ready; it accepts a quick iron and holds its shape all day."
        ),
        "base_price": Decimal("2299"), "mrp": None,
        "tags": ["mangalgiri", "cotton", "handloom", "office", "south-indian"],
        "featured": False, "is_bestseller": False, "is_new": True,
        "categories": ["mangalgiri-sarees", "pattu", "office", "south-indian", "mustard", "under-5k"],
        "variants": [
            {"sku": "OOR-MNG-MUS-01", "color": "Mustard", "fabric": "Mangalgiri Cotton", "is_default": True,  "stock": 14},
            {"sku": "OOR-MNG-GRY-01", "color": "Grey",    "fabric": "Mangalgiri Cotton", "is_default": False, "stock": 10},
        ],
        "image_count": 2,
    },
    # --- Pattu › Harini Pattu ----------------------------------------------
    {
        "slug": "harini-pattu-soft-silk-rose",
        "name": "Harini Pattu Soft Silk Saree — Rose Pink",
        "short_description": "Featherlight soft-silk pattu with a satin sheen and thin zari border.",
        "description": (
            "A soft-silk Harini Pattu in rose pink — weighted to fall beautifully yet "
            "light enough for all-day wear. A thin gold-zari border and tonal pallu keep "
            "it understated for daytime functions and house celebrations."
        ),
        "base_price": Decimal("7499"), "mrp": Decimal("8999"),
        "tags": ["harini", "pattu", "soft-silk", "festive", "south-indian"],
        "featured": False, "is_bestseller": False, "is_new": True,
        "categories": ["harini-pattu", "pattu", "festive", "south-indian", "pink", "5k-15k"],
        "variants": [
            {"sku": "OOR-HRP-ROS-01", "color": "Rose Pink", "fabric": "Soft Silk", "is_default": True,  "stock": 9},
            {"sku": "OOR-HRP-PCH-01", "color": "Peach",     "fabric": "Soft Silk", "is_default": False, "stock": 7},
        ],
        "image_count": 2,
    },
    # --- Cotton Sarees ------------------------------------------------------
    {
        "slug": "cotton-handloom-indigo",
        "name": "Pure Cotton Handloom Saree — Indigo",
        "short_description": "Crisp handloom cotton with a thin contrast border. Office-ready in minutes.",
        "description": (
            "100% handloom cotton in deep indigo with a one-inch contrast border. Stays "
            "starched through long meetings, accepts a quick iron well, and pairs with "
            "simple stud earrings for an effortless workday drape."
        ),
        "base_price": Decimal("1999"), "mrp": None,
        "tags": ["cotton", "office", "handloom", "daily"],
        "featured": False, "is_bestseller": True, "is_new": False,
        "categories": ["cotton-sarees", "office", "daily-wear", "navy", "under-5k"],
        "variants": [
            {"sku": "OOR-CTN-IND-01", "color": "Indigo",   "fabric": "Cotton Handloom", "is_default": True,  "stock": 15},
            {"sku": "OOR-CTN-CHR-01", "color": "Charcoal", "fabric": "Cotton Handloom", "is_default": False, "stock": 12},
            {"sku": "OOR-CTN-OLV-01", "color": "Olive",    "fabric": "Cotton Handloom", "is_default": False, "stock": 10},
        ],
        "image_count": 2,
    },
    # --- Banaras Sarees (x2) -----------------------------------------------
    {
        "slug": "banaras-katan-silk-crimson",
        "name": "Banarasi Katan Silk Saree — Crimson",
        "short_description": "Heavy katan silk with intricate kadwa weave across body and pallu.",
        "description": (
            "Pure katan silk woven in Banaras with the painstaking kadwa technique, where "
            "every motif is woven separately rather than floated. The result holds its "
            "shape, drapes regally, and reads as heirloom — a statement bridal piece."
        ),
        "base_price": Decimal("35000"), "mrp": Decimal("42000"),
        "tags": ["banarasi", "silk", "wedding", "bridal", "katan", "kadwa"],
        "featured": True, "is_bestseller": True, "is_new": False,
        "categories": ["banaras-sarees", "wedding", "bridal", "north-indian", "red", "15k-50k", "wedding-edit"],
        "variants": [
            {"sku": "OOR-BNK-CRM-01", "color": "Crimson Red", "fabric": "Banarasi Katan Silk", "is_default": True,  "stock": 4},
            {"sku": "OOR-BNK-MAR-01", "color": "Maroon",      "fabric": "Banarasi Katan Silk", "is_default": False, "stock": 3},
        ],
        "image_count": 3,
    },
    {
        "slug": "banaras-gold-tissue",
        "name": "Banarasi Gold Tissue Silk Saree",
        "short_description": "Lightweight tissue silk with intricate Mughal-era jaal work.",
        "description": (
            "Spun-gold tissue silk woven in Varanasi with a delicate jaal of paisleys and "
            "floral booti. The fabric catches light beautifully, making it a stand-out for "
            "Diwali, Karva Chauth, and engagement evenings."
        ),
        "base_price": Decimal("18500"), "mrp": Decimal("21000"),
        "tags": ["banarasi", "silk", "festive", "tissue", "north-indian", "diwali"],
        "featured": True, "is_bestseller": False, "is_new": False,
        "categories": ["banaras-sarees", "festive", "north-indian", "gold", "15k-50k", "diwali-2026"],
        "variants": [
            {"sku": "OOR-BNT-GLD-01", "color": "Gold",         "fabric": "Banarasi Tissue", "is_default": True,  "stock": 6},
            {"sku": "OOR-BNT-AGD-01", "color": "Antique Gold", "fabric": "Banarasi Tissue", "is_default": False, "stock": 4},
        ],
        "image_count": 3,
    },
    # --- Designer Sarees ----------------------------------------------------
    {
        "slug": "designer-embroidered-wine",
        "name": "Designer Embroidered Saree — Wine",
        "short_description": "Contemporary georgette with hand-embroidered sequin and thread work.",
        "description": (
            "A modern designer drape in wine georgette, finished with hand-embroidered "
            "sequin and resham thread work along the border and pallu. Comes with a "
            "designer blouse piece — engineered for reception and cocktail evenings."
        ),
        "base_price": Decimal("9999"), "mrp": Decimal("12999"),
        "tags": ["designer", "embroidered", "festive", "sequin", "georgette"],
        "featured": True, "is_bestseller": False, "is_new": True,
        "categories": ["designer-sarees", "festive", "wedding", "multicolor", "5k-15k"],
        "variants": [
            {"sku": "OOR-DSG-WIN-01", "color": "Wine",       "fabric": "Designer Georgette", "is_default": True,  "stock": 7},
            {"sku": "OOR-DSG-EMR-01", "color": "Emerald",    "fabric": "Designer Georgette", "is_default": False, "stock": 5},
        ],
        "image_count": 3,
    },
    # --- Kalamkari Sarees ---------------------------------------------------
    {
        "slug": "kalamkari-handpainted-cream",
        "name": "Kalamkari Hand-Painted Saree — Cream",
        "short_description": "Pen-Kalamkari narrative motifs hand-painted on a cream cotton body.",
        "description": (
            "Authentic pen (srikalahasti) Kalamkari, hand-drawn with a bamboo pen and "
            "natural dyes on a cream cotton body. Temple and floral narrative panels run "
            "along the pallu — every saree is unique to the artist's hand."
        ),
        "base_price": Decimal("4299"), "mrp": None,
        "tags": ["kalamkari", "hand-painted", "cotton", "daily", "south-indian"],
        "featured": False, "is_bestseller": False, "is_new": True,
        "categories": ["kalamkari-sarees", "daily-wear", "festive", "south-indian", "cream", "under-5k"],
        "variants": [
            {"sku": "OOR-KLM-CRM-01", "color": "Cream",    "fabric": "Kalamkari Cotton", "is_default": True,  "stock": 11},
            {"sku": "OOR-KLM-MUS-01", "color": "Mustard",  "fabric": "Kalamkari Cotton", "is_default": False, "stock": 8},
        ],
        "image_count": 2,
    },
    # --- Pure Kanjivaram Silk -----------------------------------------------
    {
        "slug": "pure-kanjivaram-bridal-maroon",
        "name": "Pure Kanjivaram Bridal Silk Saree — Maroon",
        "short_description": "Hand-woven pure mulberry silk with classic temple-border zari work.",
        "description": (
            "A heritage piece for the modern bride. Woven by master weavers using pure "
            "mulberry silk and tested zari, with the classic Mayil Chakkaram motif through "
            "the body and a wide gold-zari pallu. Comes with a matching unstitched blouse."
        ),
        "base_price": Decimal("52000"), "mrp": Decimal("60000"),
        "tags": ["kanjivaram", "kanchipuram", "silk", "bridal", "wedding", "zari", "south-indian"],
        "featured": True, "is_bestseller": True, "is_new": False,
        "categories": ["pure-kanjivaram-silk", "bridal", "wedding", "south-indian", "maroon", "above-50k", "wedding-edit"],
        "variants": [
            {"sku": "OOR-PKJ-MAR-01", "color": "Maroon",     "fabric": "Pure Kanjivaram Silk", "is_default": True,  "stock": 3},
            {"sku": "OOR-PKJ-RED-01", "color": "Deep Red",   "fabric": "Pure Kanjivaram Silk", "is_default": False, "stock": 2},
            {"sku": "OOR-PKJ-BLU-01", "color": "Royal Blue", "fabric": "Pure Kanjivaram Silk", "is_default": False, "stock": 2},
        ],
        "image_count": 3,
    },
    # --- Fancy Sarees -------------------------------------------------------
    {
        "slug": "fancy-georgette-floral",
        "name": "Fancy Georgette Floral Saree",
        "short_description": "Flowing georgette with digital florals and a tassel-finished pallu.",
        "description": (
            "Pure georgette in a bright multicolour floral with a contrast satin border "
            "and a tassel-finished pallu. Drapes light, packs light — an easy throw-on for "
            "casual outings and short trips."
        ),
        "base_price": Decimal("2799"), "mrp": None,
        "tags": ["fancy", "georgette", "floral", "casual", "lightweight"],
        "featured": False, "is_bestseller": False, "is_new": False,
        "categories": ["fancy-sarees", "casual", "daily-wear", "multicolor", "under-5k"],
        "variants": [
            {"sku": "OOR-FNG-MUL-01", "color": "Multicolor",  "fabric": "Pure Georgette", "is_default": True,  "stock": 14},
            {"sku": "OOR-FNG-PNK-01", "color": "Pink-Yellow", "fabric": "Pure Georgette", "is_default": False, "stock": 10},
        ],
        "image_count": 2,
    },
    # --- Kanchipattu --------------------------------------------------------
    {
        "slug": "kanchipattu-traditional-green",
        "name": "Kanchipattu Traditional Silk Saree — Bottle Green",
        "short_description": "Traditional Kanchipattu silk with a contrast korvai border and rich pallu.",
        "description": (
            "A traditional Kanchipattu in bottle green with a contrast magenta korvai "
            "border woven in by the three-shuttle technique, and a gold-zari pallu of "
            "annam (swan) motifs. A classic temple-wedding silk at an accessible weight."
        ),
        "base_price": Decimal("21999"), "mrp": Decimal("25999"),
        "tags": ["kanchipattu", "kanchipuram", "silk", "festive", "wedding", "south-indian"],
        "featured": False, "is_bestseller": True, "is_new": False,
        "categories": ["kanchi-pattu-saree", "festive", "wedding", "south-indian", "green", "15k-50k", "diwali-2026"],
        "variants": [
            {"sku": "OOR-KPT-GRN-01", "color": "Bottle Green", "fabric": "Kanchipattu Silk", "is_default": True,  "stock": 5},
            {"sku": "OOR-KPT-MAG-01", "color": "Magenta",      "fabric": "Kanchipattu Silk", "is_default": False, "stock": 4},
        ],
        "image_count": 3,
    },
    # --- Cocktail & Party Wear ----------------------------------------------
    {
        "slug": "cocktail-sequin-party-black",
        "name": "Cocktail Sequin Party Saree — Black",
        "short_description": "Pre-draped-friendly sequin saree with a stitched-ready pallu for evenings.",
        "description": (
            "A full-sequin party saree in classic black on a soft net base, with a "
            "ready-to-pleat finish and a designer blouse piece. Built for cocktail "
            "evenings, sangeets, and receptions where you want to shimmer."
        ),
        "base_price": Decimal("8499"), "mrp": Decimal("10999"),
        "tags": ["cocktail", "party", "sequin", "festive", "designer"],
        "featured": False, "is_bestseller": False, "is_new": True,
        "categories": ["cocktail-party-wear-sarees", "festive", "navy", "5k-15k"],
        "variants": [
            {"sku": "OOR-CCK-BLK-01", "color": "Black",     "fabric": "Sequin Net", "is_default": True,  "stock": 8},
            {"sku": "OOR-CCK-NVY-01", "color": "Navy",      "fabric": "Sequin Net", "is_default": False, "stock": 6},
            {"sku": "OOR-CCK-WIN-01", "color": "Wine",      "fabric": "Sequin Net", "is_default": False, "stock": 5},
        ],
        "image_count": 2,
    },
]


# All seeded products default to PUBLISHED status — they should be live on
# the catalog read API immediately.
DEFAULT_PRODUCT_STATUS = ProductStatus.PUBLISHED


# ---------------------------------------------------------------------------
# Banners
# ---------------------------------------------------------------------------

BANNERS: list[dict[str, Any]] = [
    {
        "title": "Diwali Collection 2026",
        "subtitle": "Festive sarees crafted on traditional looms across India",
        "placement": BannerPlacement.HOMEPAGE_HERO,
        "cta_label": "Shop the edit",
        "cta_url": "/saris?collection=diwali-2026",
        "image_seed": "oorvashee-banner-diwali-hero",
        "category_slug": "diwali-2026",
        "display_order": 1,
    },
    {
        "title": "Wedding Edit",
        "subtitle": "Bridal Kanjivarams, Banarasis, and heirloom silks",
        "placement": BannerPlacement.HOMEPAGE_SECONDARY,
        "cta_label": "Explore",
        "cta_url": "/saris?collection=wedding-edit",
        "image_seed": "oorvashee-banner-wedding",
        "category_slug": "wedding-edit",
        "display_order": 2,
    },
    {
        "title": "The Bridal Edit",
        "subtitle": "Heirloom-grade silks for the modern bride",
        "placement": BannerPlacement.CATEGORY_TOP,
        "cta_label": "Browse bridal",
        "cta_url": "/saris?occasion=bridal",
        "image_seed": "oorvashee-banner-bridal-top",
        "category_slug": "bridal",
        "display_order": 1,
    },
    {
        "title": "Free shipping on orders above ₹3,000",
        "subtitle": "Pan-India delivery in 3–5 business days",
        "placement": BannerPlacement.CART_PROMO,
        "cta_label": None,
        "cta_url": None,
        "image_seed": None,
        "category_slug": None,
        "display_order": 1,
    },
]
