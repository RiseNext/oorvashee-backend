"""API v1 router aggregator.

Every router included here gets the `/api/v1` prefix in `app/main.py`.
Adding a new resource is one line — no edits to `main.py` needed.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers import health
from app.routers.account import cart as account_cart
from app.routers.account import orders as account_orders
from app.routers.account import wishlist as account_wishlist
from app.routers.admin import media as admin_media
from app.routers.public import categories as public_categories
from app.routers.public import checkout as public_checkout
from app.routers.public import orders as public_orders
from app.routers.public import payments as public_payments
from app.routers.public import products as public_products
from app.routers.webhooks import clerk as webhook_clerk
from app.routers.webhooks import razorpay as webhook_razorpay

api_v1_router = APIRouter()

# ---- Health ------------------------------------------------------------------
api_v1_router.include_router(health.router)

# ---- Public catalog ---------------------------------------------------------
api_v1_router.include_router(
    public_products.router, prefix="/products", tags=["products"]
)
api_v1_router.include_router(
    public_categories.router, prefix="/categories", tags=["categories"]
)

# ---- Public checkout + payments + tracking ----------------------------------
api_v1_router.include_router(
    public_checkout.router, prefix="/checkout", tags=["checkout"]
)
api_v1_router.include_router(
    public_payments.router, prefix="/payments", tags=["payments"]
)
api_v1_router.include_router(
    public_orders.router, prefix="/orders", tags=["orders"]
)

# ---- Account (authenticated) ------------------------------------------------
api_v1_router.include_router(
    account_cart.router, prefix="/account/cart", tags=["account:cart"]
)
api_v1_router.include_router(
    account_wishlist.router, prefix="/account/wishlist", tags=["account:wishlist"]
)
api_v1_router.include_router(
    account_orders.router, prefix="/account/orders", tags=["account:orders"]
)

# ---- Webhooks (signature-verified) ------------------------------------------
api_v1_router.include_router(
    webhook_razorpay.router, prefix="/webhooks", tags=["webhooks"]
)
api_v1_router.include_router(
    webhook_clerk.router, prefix="/webhooks", tags=["webhooks"]
)

# ---- Admin ------------------------------------------------------------------
# Media router carries its own require_role("admin") gate; the prefix is
# /admin (not /admin/media) because the router exposes both /media/sign and
# /products/{id}/images under the same admin namespace.
api_v1_router.include_router(
    admin_media.router, prefix="/admin", tags=["admin:media"]
)

# ---- Admin: product / order CRUD (Phase 3 — next cycle) ---------------------
# api_v1_router.include_router(
#     admin_products.router, prefix="/admin/products", tags=["admin:products"]
# )

__all__ = ["api_v1_router"]
