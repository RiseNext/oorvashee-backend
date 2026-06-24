"""Courier dispatch endpoints — slim payload, locked to the `courier` role.

Router-level `require_role("courier")` gates EVERY route here (mirrors the admin
routers). The role is read from the verified Clerk JWT claim (security.py), so:
  - no token            → 401
  - admin / customer    → 403 (their role is not "courier")
  - courier             → allowed

A courier can ONLY list paid orders for dispatch and set an AWB. There is no
endpoint here that exposes payment, totals, email, billing, items, or any admin
action — and the response_model on each route is the slim courier schema.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.courier import CourierOrder, SetAwbRequest
from app.services.courier_service import CourierService

router = APIRouter(dependencies=[Depends(require_role("courier", "admin"))])

CurrentCourier = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get(
    "/orders",
    response_model=list[CourierOrder],
    summary="Paid orders for dispatch (slim, delivery-only payload)",
)
async def list_orders(
    session: DbSession,
    _courier: CurrentCourier,
) -> list[CourierOrder]:
    return await CourierService(session).list_dispatch_orders()


@router.post(
    "/orders/{order_number}/awb",
    response_model=CourierOrder,
    summary="Set AWB / tracking on a Ready order (packed → shipped)",
)
async def set_awb(
    request: Request,
    body: SetAwbRequest,
    session: DbSession,
    courier: CurrentCourier,
    order_number: Annotated[str, Path(max_length=32)],
) -> CourierOrder:
    return await CourierService(session).set_awb(
        order_number,
        body,
        actor_user_id=courier.id,
        request_id=_request_id(request),
    )
