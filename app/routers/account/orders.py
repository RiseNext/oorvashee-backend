"""Authenticated order endpoints — account history + detail."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
)
from app.core.security import get_current_user
from app.db.deps import DbSession
from app.models.user import User
from app.repositories.order_repo import OrderRepository
from app.schemas.checkout import OrderRead
from app.schemas.tracking import OrderTracking
from app.services.checkout_service import CheckoutService
from app.services.order_tracking_service import OrderTrackingService

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get(
    "",
    response_model=Page[OrderRead],
    summary="List the authenticated user's orders, newest first",
)
async def list_my_orders(
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> Page[OrderRead]:
    repo = OrderRepository(session)
    orders, total = await repo.list_for_user(
        user.id, limit=page_size, offset=(page - 1) * page_size
    )
    items = [CheckoutService.serialize_order(o) for o in orders]
    return Page[OrderRead](items=items, next_cursor=None, total=total)


@router.get(
    "/{order_number}",
    response_model=OrderRead,
    summary="Full order detail (authenticated user can only access own orders)",
)
async def get_my_order(
    order_number: str, user: CurrentUser, session: DbSession
) -> OrderRead:
    order = await OrderRepository(session).get_by_number(order_number)
    if order is None:
        raise NotFoundError("Order not found")
    if order.user_id != user.id:
        # Don't reveal existence of someone else's order — same 404 surface.
        raise PermissionDeniedError("Not authorised to view this order")
    return CheckoutService.serialize_order(order)


@router.get(
    "/{order_number}/tracking",
    response_model=OrderTracking,
    summary="Live courier tracking for the authenticated user's own order",
)
async def track_my_order(
    order_number: str, user: CurrentUser, session: DbSession
) -> OrderTracking:
    order = await OrderRepository(session).get_by_number(order_number)
    if order is None:
        raise NotFoundError("Order not found")
    if order.user_id != user.id:
        raise PermissionDeniedError("Not authorised to view this order")
    return await OrderTrackingService(session).for_order(order)
