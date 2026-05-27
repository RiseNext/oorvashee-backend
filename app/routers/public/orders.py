"""Public order tracking — guests look up their order with order_number + email.

Phase 2G adds explicit IP-level rate limiting on top of the existing
combinatorial defence (random 32-bit suffix in the order number + email
match). A scraper hitting this endpoint gets boxed into 30/min per IP.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.core.exceptions import NotFoundError
from app.core.rate_limit import profiles
from app.db.deps import DbSession
from app.repositories.order_repo import OrderRepository
from app.schemas.checkout import OrderRead
from app.services.checkout_service import CheckoutService

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
    if order is None or order.email.lower() != email.lower():
        # Single 404 surface — never reveal whether order exists with mismatched email.
        raise NotFoundError("Order not found")
    return CheckoutService.serialize_order(order)
