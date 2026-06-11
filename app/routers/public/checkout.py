"""Public checkout endpoints (works for guests + authenticated users)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import get_settings
from app.core.idempotency import IdempotencyDep, with_idempotency
from app.core.rate_limit import profiles
from app.core.security import (
    Principal,
    get_current_principal,
    get_current_principal_optional,
    get_current_user,
)
from app.db.deps import DbSession
from app.schemas.checkout import (
    CheckoutQuoteRead,
    CheckoutQuoteRequest,
    CheckoutSessionRead,
    PlaceOrderRead,
    PlaceOrderRequest,
    StartPaymentRequest,
)
from app.services.checkout_payment_service import CheckoutPaymentService
from app.services.checkout_service import CheckoutService
from app.services.checkout_session_service import CheckoutSessionService

router = APIRouter()

OptionalPrincipal = Annotated[Principal | None, Depends(get_current_principal_optional)]
RequiredPrincipal = Annotated[Principal, Depends(get_current_principal)]


@router.post(
    "",
    response_model=CheckoutSessionRead,
    summary="Start checkout: atomically reserve the authed user's cart (~3 min hold).",
)
@profiles.checkout()
async def start_checkout(
    request: Request,
    response: Response,
    session: DbSession,
    principal: RequiredPrincipal,
) -> CheckoutSessionRead:
    """Authed-only. Reserves every line in the user's server cart in one
    row-locked transaction. Reuses the user's existing live session if any
    (one active session per user). Returns per-line availability + a 3-minute
    expiry; raises 409 (reservation_conflict) if any line is unavailable.
    """
    user = await get_current_user(principal, session)
    return await CheckoutSessionService(session).start_checkout(user_id=user.id)


@router.post(
    "/{session_id}/pay",
    response_model=PlaceOrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Initiate Razorpay payment for a reserved checkout session.",
)
@profiles.checkout()
async def start_payment(
    request: Request,
    response: Response,
    session_id: uuid.UUID,
    body: StartPaymentRequest,
    session: DbSession,
    principal: RequiredPrincipal,
) -> PlaceOrderRead:
    """Authed-only. Transitions the session's reservations to
    PAYMENT_PROCESSING (15-min backstop), creates the PENDING order, and
    returns the Razorpay handoff. Physical stock is decremented only on the
    capture webhook.
    """
    user = await get_current_user(principal, session)
    return await CheckoutPaymentService(session).start_payment(
        session_id=session_id, user_id=user.id, req=body
    )


@router.post(
    "/{session_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Release a checkout reservation (user cancelled / Razorpay dismissed).",
)
@profiles.checkout()
async def cancel_checkout(
    request: Request,
    response: Response,
    session_id: uuid.UUID,
    session: DbSession,
    principal: RequiredPrincipal,
) -> Response:
    """Authed-only. Cancels the session + its reservations + any unpaid order
    and frees the held stock immediately. Idempotent."""
    user = await get_current_user(principal, session)
    await CheckoutSessionService(session).cancel(
        session_id=session_id, user_id=user.id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    summary="[DEPRECATED] Legacy order-at-checkout. Disabled by default — use "
    "POST /checkout then POST /checkout/{session_id}/pay.",
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
    # D1: the legacy flow uses the stored inventory.reserved ledger, which the
    # new derived-reservation flow can't see — running both can oversell. It is
    # OFF unless explicitly re-enabled for a rollback.
    if not get_settings().legacy_checkout_enabled:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "This checkout endpoint is retired. Reserve via POST /checkout, "
                "then pay via POST /checkout/{session_id}/pay."
            ),
        )

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
