"""Public checkout endpoints (works for guests + authenticated users)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.core.idempotency import IdempotencyDep, with_idempotency
from app.core.rate_limit import profiles
from app.core.security import Principal, get_current_principal_optional
from app.db.deps import DbSession
from app.schemas.checkout import (
    CheckoutQuoteRead,
    CheckoutQuoteRequest,
    PlaceOrderRead,
    PlaceOrderRequest,
)
from app.services.checkout_service import CheckoutService

router = APIRouter()

OptionalPrincipal = Annotated[Principal | None, Depends(get_current_principal_optional)]


@router.post(
    "/quote",
    response_model=CheckoutQuoteRead,
    summary="Server-recompute totals + availability for the given line items",
)
async def checkout_quote(
    body: CheckoutQuoteRequest, session: DbSession
) -> CheckoutQuoteRead:
    return await CheckoutService(session).quote(body)


@router.post(
    "/orders",
    response_model=PlaceOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order (COD or Razorpay). Requires Idempotency-Key.",
)
@profiles.checkout()
async def place_order(
    request: Request,
    body: PlaceOrderRequest,
    session: DbSession,
    response: Response,
    ctx: IdempotencyDep,
    principal: OptionalPrincipal,
) -> dict:
    user_id = None
    if principal is not None:
        # Auto-provision a user row via the same dependency the cart routes use.
        from app.core.security import get_current_user

        user = await get_current_user(principal, session)  # type: ignore[arg-type]
        user_id = user.id

    async def execute() -> dict:
        result = await CheckoutService(session).place_order(body, user_id=user_id)
        return result.model_dump(mode="json")

    status_code, payload = await with_idempotency(
        ctx=ctx,
        session=session,
        user_id=user_id,
        status_code=status.HTTP_201_CREATED,
        execute=execute,
    )
    response.status_code = status_code
    return payload
