"""API v1 router aggregator.

Every router included here gets the `/api/v1` prefix in `app/main.py`.
Adding a new resource is one line — no edits to `main.py` needed.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers import health
from app.routers.public import categories as public_categories
from app.routers.public import products as public_products

api_v1_router = APIRouter()

# ---- Health (no prefix; /api/v1/health and /api/v1/health/live) -------------
api_v1_router.include_router(health.router)

# ---- Public catalog (Cycle 1) ----------------------------------------------
api_v1_router.include_router(
    public_products.router, prefix="/products", tags=["products"]
)
api_v1_router.include_router(
    public_categories.router, prefix="/categories", tags=["categories"]
)

# ---- Cart / checkout (Cycle 2 — next turn) ----------------------------------
# api_v1_router.include_router(public.cart.router, prefix="/cart", tags=["cart"])
# api_v1_router.include_router(public.checkout.router, prefix="/checkout", tags=["checkout"])

# ---- Account (Cycle 4) ------------------------------------------------------
# api_v1_router.include_router(account.me.router, prefix="/account", tags=["account"])

# ---- Admin (Cycle 3) --------------------------------------------------------
# api_v1_router.include_router(
#     admin.products.router, prefix="/admin/products", tags=["admin:products"]
# )

# ---- Webhooks (Cycles 2 & 4) ------------------------------------------------
# api_v1_router.include_router(webhooks.razorpay.router, prefix="/webhooks", tags=["webhooks"])
# api_v1_router.include_router(webhooks.clerk.router, prefix="/webhooks", tags=["webhooks"])

__all__ = ["api_v1_router"]
