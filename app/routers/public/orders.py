"""Public order tracking — guests look up their order with order_number + email.

Phase 2G adds explicit IP-level rate limiting on top of the existing
combinatorial defence (random 32-bit suffix in the order number + email
match). A scraper hitting this endpoint gets boxed into 30/min per IP.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.core.exceptions import NotFoundError
from app.core.rate_limit import profiles
from app.db.deps import DbSession
from app.repositories.order_repo import OrderRepository
from app.schemas.checkout import OrderRead
from app.schemas.tracking import OrderTracking
from app.services.checkout_service import CheckoutService
from app.services.order_tracking_service import OrderTrackingService

router = APIRouter()


@router.get(
    "/{order_number}",
    response_model=OrderRead,
    summary="Anonymous order tracking — order_number + email must match",
)
@profiles.tracking()
async def get_order_by_number(
    request: Request,
    response: Response,
    order_number: str,
    session: DbSession,
    email: Annotated[str, Query(min_length=3, max_length=320)],
) -> OrderRead:
    order = await OrderRepository(session).get_by_number(order_number)
    if order is None or not hmac.compare_digest(
        order.email.lower().encode("utf-8"), email.lower().encode("utf-8")
    ):
        # Single 404 surface — never reveal whether order exists with mismatched email.
        # Constant-time compare avoids a per-byte timing oracle on the stored email.
        raise NotFoundError("Order not found")
    # Slim, PII-minimised projection for the anonymous tracking surface (no phone,
    # no street address, no billing) — the authenticated account view keeps the full one.
    return CheckoutService.serialize_order_public(order)


@router.get(
    "/{order_number}/tracking",
    response_model=OrderTracking,
    summary="Live courier tracking — order_number + email must match",
)
@profiles.tracking()
async def track_order_by_number(
    request: Request,
    response: Response,
    order_number: str,
    session: DbSession,
    email: Annotated[str, Query(min_length=3, max_length=320)],
) -> OrderTracking:
    order = await OrderRepository(session).get_by_number(order_number)
    if order is None or not hmac.compare_digest(
        order.email.lower().encode("utf-8"), email.lower().encode("utf-8")
    ):
        # Same single 404 surface + constant-time email compare as the order
        # lookup above. The tracking payload carries NO PII (awb/status/events only).
        raise NotFoundError("Order not found")
    return await OrderTrackingService(session).for_order(order)
