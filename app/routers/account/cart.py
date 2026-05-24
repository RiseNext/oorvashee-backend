"""Authenticated cart endpoints.

All routes require a valid Clerk JWT; the `User` row is auto-provisioned
on first call via `get_current_user`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.security import get_current_user
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.cart import (
    AddToCartRequest,
    CartRead,
    MergeCartRequest,
    UpdateCartItemRequest,
)
from app.services.cart_service import CartService

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=CartRead, summary="Get authenticated user's cart")
async def get_cart(user: CurrentUser, session: DbSession) -> CartRead:
    return await CartService(session).get_cart(user.id)


@router.post(
    "/items",
    response_model=CartRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add (or increment) a variant in the cart",
)
async def add_item(
    body: AddToCartRequest, user: CurrentUser, session: DbSession
) -> CartRead:
    return await CartService(session).add_item(user.id, body.variant_id, body.quantity)


@router.patch(
    "/items/{item_id}",
    response_model=CartRead,
    summary="Set the quantity of an existing cart line",
)
async def update_item(
    item_id: uuid.UUID,
    body: UpdateCartItemRequest,
    user: CurrentUser,
    session: DbSession,
) -> CartRead:
    return await CartService(session).update_item(user.id, item_id, body.quantity)


@router.delete(
    "/items/{item_id}",
    response_model=CartRead,
    summary="Remove a cart line",
)
async def remove_item(
    item_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> CartRead:
    return await CartService(session).remove_item(user.id, item_id)


@router.delete("", response_model=CartRead, summary="Clear the entire cart")
async def clear_cart(user: CurrentUser, session: DbSession) -> CartRead:
    return await CartService(session).clear(user.id)


@router.post(
    "/merge",
    response_model=CartRead,
    summary="Merge a guest cart (from localStorage) after sign-in",
)
async def merge_cart(
    body: MergeCartRequest, user: CurrentUser, session: DbSession
) -> CartRead:
    return await CartService(session).merge(user.id, body.items)
