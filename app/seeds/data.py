"""Declarative seed data — categories, products, banners.

The shape is intentionally plain dicts so non-engineers can edit it.
The loaders in `runner.py` resolve cross-references (e.g. product → category
slug → category id) at apply time.
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
# Categories — taxonomy axes that filter the catalog.
# ---------------------------------------------------------------------------

CATEGORIES: list[dict[str, Any]] = [
    # --- Fabric ---
    {"slug": "kanchipuram-silk", "name": "Kanchipuram Silk",  "kind": CategoryKind.FABRIC, "display_order": 1},
    {"slug": "banarasi-silk",    "name": "Banarasi Silk",     "kind": CategoryKind.FABRIC, "display_order": 2},
    {"slug": "mysore-silk",      "name": "Mysore Silk",       "kind": CategoryKind.FABRIC, "display_order": 3},
    {"slug": "tussar-silk",      "name": "Tussar Silk",       "kind": CategoryKind.FABRIC, "display_order": 4},
    {"slug": "chanderi",         "name": "Chanderi",          "kind": CategoryKind.FABRIC, "display_order": 5},
    {"slug": "patola",           "name": "Patola",            "kind": CategoryKind.FABRIC, "display_order": 6},
    {"slug": "georgette",        "name": "Georgette",         "kind": CategoryKind.FABRIC, "display_order": 7},
    {"slug": "cotton",           "name": "Cotton",            "kind": CategoryKind.FABRIC, "display_order": 8},
    {"slug": "linen",            "name": "Linen",             "kind": CategoryKind.FABRIC, "display_order": 9},

    # --- Occasion ---
    {"slug": "bridal",      "name": "Bridal",      "kind": CategoryKind.OCCASION, "display_order": 1},
    {"slug": "wedding",     "name": "Wedding",     "kind": CategoryKind.OCCASION, "display_order": 2},
    {"slug": "festive",     "name": "Festive",     "kind": CategoryKind.OCCASION, "display_order": 3},
    {"slug": "daily-wear",  "name": "Daily Wear",  "kind": CategoryKind.OCCASION, "display_order": 4},
    {"slug": "office",      "name": "Office",      "kind": CategoryKind.OCCASION, "display_order": 5},
    {"slug": "casual",      "name": "Casual",      "kind": CategoryKind.OCCASION, "display_order": 6},

    # --- Region ---
    {"slug": "south-indian", "name": "South Indian", "kind": CategoryKind.REGION, "display_order": 1},
    {"slug": "north-indian", "name": "North Indian", "kind": CategoryKind.REGION, "display_order": 2},

    # --- Colour ---
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

    # --- Price bracket ---
    {"slug": "under-5k",   "name": "Under ₹5,000",      "kind": CategoryKind.PRICE_BRACKET, "display_order": 1},
    {"slug": "5k-15k",     "name": "₹5,000 – ₹15,000",  "kind": CategoryKind.PRICE_BRACKET, "display_order": 2},
    {"slug": "15k-50k",    "name": "₹15,000 – ₹50,000", "kind": CategoryKind.PRICE_BRACKET, "display_order": 3},
    {"slug": "above-50k",  "name": "Above ₹50,000",     "kind": CategoryKind.PRICE_BRACKET, "display_order": 4},

    # --- Collections ---
    {"slug": "diwali-2026",   "name": "Diwali 2026",  "kind": CategoryKind.COLLECTION, "display_order": 1},
    {"slug": "wedding-edit",  "name": "Wedding Edit", "kind": CategoryKind.COLLECTION, "display_order": 2},
]


# ---------------------------------------------------------------------------
# Products — 10 realistic sarees with variants + inventory + images.
# ---------------------------------------------------------------------------

PRODUCTS: list[dict[str, Any]] = [
    {
        "slug": "kanchipuram-bridal-maroon",
        "name": "Kanchipuram Bridal Silk Saree — Maroon",
        "short_description": "Hand-woven pure mulberry silk with classic temple-border zari work, made on traditional looms in Kanchipuram.",
        "description": (
            "A heritage piece for the modern bride. This Kanchipuram silk saree is "
            "woven by master weavers using pure mulberry silk and tested zari, with "
            "the classic Mayil Chakkaram motif running through the body and a wide "
            "gold-zari pallu. Comes with an unstitched blouse piece in matching maroon."
        ),
        "base_price": Decimal("24999"),
        "mrp": Decimal("29999"),
        "tags": ["bridal", "kanchipuram", "silk", "south-indian", "zari", "wedding"],
        "featured": True,
        "is_bestseller": True,
        "is_new": False,
        "categories": ["kanchipuram-silk", "bridal", "south-indian", "maroon", "15k-50k", "wedding-edit"],
        "variants": [
            {"sku": "OOR-KCB-MAR-01", "color": "Maroon",     "fabric": "Kanchipuram Silk", "is_default": True,  "stock": 8},
            {"sku": "OOR-KCB-RED-01", "color": "Deep Red",   "fabric": "Kanchipuram Silk", "is_default": False, "stock": 5},
            {"sku": "OOR-KCB-BLU-01", "color": "Royal Blue", "fabric": "Kanchipuram Silk", "is_default": False, "stock": 3},
        ],
        "image_count": 3,
    },
    {
        "slug": "banarasi-gold-tissue",
        "name": "Banarasi Gold Tissue Silk Saree",
        "short_description": "Lightweight tissue silk with intricate Mughal-era jaal work, ideal for festive evenings.",
        "description": (
            "Spun-gold tissue silk woven in Varanasi with a delicate jaal of paisleys "
            "and floral booti. The fabric catches light beautifully, making it a "
            "stand-out for Diwali, Karva Chauth, and engagement evenings."
        ),
        "base_price": Decimal("18500"),
        "mrp": Decimal("21000"),
        "tags": ["banarasi", "silk", "festive", "tissue", "north-indian", "diwali"],
        "featured": True,
        "is_bestseller": False,
        "is_new": False,
        "categories": ["banarasi-silk", "festive", "north-indian", "gold", "15k-50k", "diwali-2026"],
        "variants": [
            {"sku": "OOR-BGT-GLD-01", "color": "Gold",         "fabric": "Banarasi Tissue", "is_default": True, "stock": 6},
            {"sku": "OOR-BGT-AGD-01", "color": "Antique Gold", "fabric": "Banarasi Tissue", "is_default": False, "stock": 4},
        ],
        "image_count": 3,
    },
    {
        "slug": "mysore-soft-silk-pink",
        "name": "Mysore Soft Silk Saree — Rose Pink",
        "short_description": "Crepe-weighted Mysore silk with a satin sheen and minimal zari border. Light, drapeable, festive.",
        "description": (
            "Pure crepe Mysore silk in rose pink, finished with a thin gold-zari "
            "border. Falls soft and stays in place — perfect for daytime functions, "
            "house pujas, and small celebrations. Pairs with both contrast and "
            "tonal blouses."
        ),
        "base_price": Decimal("8999"),
        "mrp": None,
        "tags": ["mysore", "silk", "festive", "south-indian", "crepe"],
        "featured": False,
        "is_bestseller": False,
        "is_new": True,
        "categories": ["mysore-silk", "festive", "south-indian", "pink", "5k-15k"],
        "variants": [
            {"sku": "OOR-MSS-PNK-01", "color": "Rose Pink",     "fabric": "Mysore Silk", "is_default": True,  "stock": 10},
            {"sku": "OOR-MSS-PCH-01", "color": "Peach",         "fabric": "Mysore Silk", "is_default": False, "stock": 8},
            {"sku": "OOR-MSS-PIS-01", "color": "Pista Green",   "fabric": "Mysore Silk", "is_default": False, "stock": 6},
        ],
        "image_count": 2,
    },
    {
        "slug": "tussar-cream-daily",
        "name": "Tussar Silk Cream Daily Drape",
        "short_description": "Natural-finish tussar with hand-block prints. Comfortable for all-day wear in warm weather.",
        "description": (
            "Tussar silk woven in Bhagalpur with its signature uneven grain and warm "
            "cream base. Block-printed motifs along the pallu in earthy madder. "
            "Breathable enough for daily wear, refined enough for office and lunches."
        ),
        "base_price": Decimal("4499"),
        "mrp": None,
        "tags": ["tussar", "silk", "daily", "block-print", "north-indian"],
        "featured": False,
        "is_bestseller": False,
        "is_new": False,
        "categories": ["tussar-silk", "daily-wear", "north-indian", "cream", "under-5k"],
        "variants": [
            {"sku": "OOR-TSC-CRM-01", "color": "Cream", "fabric": "Tussar Silk", "is_default": True,  "stock": 12},
            {"sku": "OOR-TSC-BGE-01", "color": "Beige", "fabric": "Tussar Silk", "is_default": False, "stock": 9},
        ],
        "image_count": 2,
    },
    {
        "slug": "cotton-office-navy",
        "name": "Pure Cotton Office Saree — Navy",
        "short_description": "Crisp handloom cotton with a thin contrast border. Office-ready in under five minutes.",
        "description": (
            "100% handloom cotton in deep navy with a one-inch contrast border. "
            "Stays starched through long meetings, accepts a quick iron well, and "
            "looks intentional with simple stud earrings and a leather watch."
        ),
        "base_price": Decimal("2199"),
        "mrp": None,
        "tags": ["cotton", "office", "handloom", "daily"],
        "featured": False,
        "is_bestseller": False,
        "is_new": False,
        "categories": ["cotton", "office", "navy", "under-5k"],
        "variants": [
            {"sku": "OOR-CTN-NVY-01", "color": "Navy",     "fabric": "Cotton Handloom", "is_default": True,  "stock": 15},
            {"sku": "OOR-CTN-CHR-01", "color": "Charcoal", "fabric": "Cotton Handloom", "is_default": False, "stock": 12},
            {"sku": "OOR-CTN-OLV-01", "color": "Olive",    "fabric": "Cotton Handloom", "is_default": False, "stock": 10},
        ],
        "image_count": 2,
    },
    {
        "slug": "banarasi-wedding-red",
        "name": "Banarasi Wedding Silk — Crimson Red",
        "short_description": "Heavy katan silk with intricate kadwa weave across body and pallu. A statement bridal piece.",
        "description": (
            "Pure katan silk woven in Banaras with the painstaking kadwa technique, "
            "where every motif is woven separately rather than floated. The result is "
            "a saree that holds its shape, drapes regally, and reads as heirloom from "
            "the first glance."
        ),
        "base_price": Decimal("35000"),
        "mrp": Decimal("42000"),
        "tags": ["banarasi", "silk", "wedding", "bridal", "katan", "kadwa"],
        "featured": True,
        "is_bestseller": True,
        "is_new": False,
        "categories": ["banarasi-silk", "wedding", "bridal", "north-indian", "red", "15k-50k", "wedding-edit"],
        "variants": [
            {"sku": "OOR-BWR-CRM-01", "color": "Crimson Red", "fabric": "Banarasi Katan Silk", "is_default": True,  "stock": 4},
            {"sku": "OOR-BWR-MAR-01", "color": "Maroon",      "fabric": "Banarasi Katan Silk", "is_default": False, "stock": 3},
        ],
        "image_count": 3,
    },
    {
        "slug": "chanderi-pastel-green",
        "name": "Chanderi Silk Cotton — Pastel Green",
        "short_description": "Sheer Chanderi weave with delicate buti work. Light as air, elegant for daytime festive.",
        "description": (
            "Genuine Chanderi from Madhya Pradesh — a silk-cotton blend with the "
            "fabric's trademark sheen and translucency. Pastel green base with "
            "scattered silver-zari butis and a clean traditional border."
        ),
        "base_price": Decimal("6799"),
        "mrp": None,
        "tags": ["chanderi", "festive", "lightweight", "silk-cotton"],
        "featured": False,
        "is_bestseller": False,
        "is_new": True,
        "categories": ["chanderi", "festive", "north-indian", "green", "5k-15k"],
        "variants": [
            {"sku": "OOR-CHP-PGN-01", "color": "Pastel Green",  "fabric": "Chanderi Silk Cotton", "is_default": True,  "stock": 11},
            {"sku": "OOR-CHP-SKY-01", "color": "Sky Blue",      "fabric": "Chanderi Silk Cotton", "is_default": False, "stock": 8},
            {"sku": "OOR-CHP-LMY-01", "color": "Lemon Yellow",  "fabric": "Chanderi Silk Cotton", "is_default": False, "stock": 7},
        ],
        "image_count": 2,
    },
    {
        "slug": "georgette-floral-multicolor",
        "name": "Georgette Floral Print Saree",
        "short_description": "Flowing georgette with hand-finished digital florals. Throws on easily for casual outings.",
        "description": (
            "Pure georgette in a bright multicolour floral, with a contrast satin "
            "border and a tassel-finished pallu. Drapes light, packs light — a great "
            "travel saree for short trips and casual evenings."
        ),
        "base_price": Decimal("3299"),
        "mrp": None,
        "tags": ["georgette", "floral", "daily", "casual", "lightweight"],
        "featured": False,
        "is_bestseller": False,
        "is_new": False,
        "categories": ["georgette", "daily-wear", "casual", "multicolor", "under-5k"],
        "variants": [
            {"sku": "OOR-GFL-MUL-01", "color": "Multicolor",   "fabric": "Pure Georgette", "is_default": True,  "stock": 14},
            {"sku": "OOR-GFL-PNY-01", "color": "Pink-Yellow",  "fabric": "Pure Georgette", "is_default": False, "stock": 10},
        ],
        "image_count": 2,
    },
    {
        "slug": "patola-double-ikat-royal",
        "name": "Patola Double Ikat — Royal Blue",
        "short_description": "Hand-woven Patola from Patan — one of the rarest weaves in India. A genuine collector's saree.",
        "description": (
            "Made by the Salvi family of Patan, Gujarat, where double-ikat patola "
            "weaving has been a closely-guarded family craft for centuries. Every "
            "thread is hand-dyed and aligned before weaving — a single saree takes "
            "six to twelve months. Royal blue base with the geometric Pan-Bhat pattern."
        ),
        "base_price": Decimal("52000"),
        "mrp": Decimal("60000"),
        "tags": ["patola", "double-ikat", "heirloom", "patan", "luxury", "wedding"],
        "featured": True,
        "is_bestseller": True,
        "is_new": False,
        "categories": ["patola", "wedding", "north-indian", "blue", "above-50k"],
        "variants": [
            {"sku": "OOR-PDI-RBL-01", "color": "Royal Blue", "fabric": "Patola Double Ikat", "is_default": True,  "stock": 2},
            {"sku": "OOR-PDI-WIN-01", "color": "Wine Red",   "fabric": "Patola Double Ikat", "is_default": False, "stock": 2},
        ],
        "image_count": 3,
    },
    {
        "slug": "linen-half-half-mustard",
        "name": "Linen Half-Half Saree — Mustard & Black",
        "short_description": "Contemporary linen weave split half-and-half for an effortless modern silhouette.",
        "description": (
            "Pure linen woven in two solid panels — mustard yellow body with a "
            "deep black pallu. Crisp, breathable, and perfectly suited for warmer "
            "months. A modern saree that wears well with both jhumkas and stud "
            "earrings."
        ),
        "base_price": Decimal("3899"),
        "mrp": None,
        "tags": ["linen", "contemporary", "daily", "casual"],
        "featured": False,
        "is_bestseller": False,
        "is_new": True,
        "categories": ["linen", "daily-wear", "casual", "mustard", "under-5k"],
        "variants": [
            {"sku": "OOR-LHH-MUB-01", "color": "Mustard & Black", "fabric": "Pure Linen", "is_default": True,  "stock": 13},
            {"sku": "OOR-LHH-INW-01", "color": "Indigo & White",  "fabric": "Pure Linen", "is_default": False, "stock": 9},
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
        "cta_url": "/shop?collection=diwali-2026",
        "image_seed": "oorvashee-banner-diwali-hero",
        "category_slug": "diwali-2026",
        "display_order": 1,
    },
    {
        "title": "Wedding Edit",
        "subtitle": "Bridal Kanchipurams, Banarasis, and heirloom Patolas",
        "placement": BannerPlacement.HOMEPAGE_SECONDARY,
        "cta_label": "Explore",
        "cta_url": "/shop?collection=wedding-edit",
        "image_seed": "oorvashee-banner-wedding",
        "category_slug": "wedding-edit",
        "display_order": 2,
    },
    {
        "title": "The Bridal Edit",
        "subtitle": "Heirloom-grade silks for the modern bride",
        "placement": BannerPlacement.CATEGORY_TOP,
        "cta_label": "Browse bridal",
        "cta_url": "/shop?occasion=bridal",
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
