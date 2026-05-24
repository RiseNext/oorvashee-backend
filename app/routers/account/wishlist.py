"""Authenticated wishlist endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.security import get_current_user
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.cart import CartRead
from app.schemas.wishlist import MoveToCartRequest, WishlistRead
from app.services.wishlist_service import WishlistService

router = APIRouter()

CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_model=WishlistRead, summary="List wishlisted products")
async def list_wishlist(user: CurrentUser, session: DbSession) -> WishlistRead:
    return await WishlistService(session).list_for_user(user.id)


@router.put(
    "/{product_id}",
    response_model=WishlistRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a product to the wishlist (idempotent)",
)
async def add_to_wishlist(
    product_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> WishlistRead:
    return await WishlistService(session).add(user.id, product_id)


@router.delete(
    "/{product_id}",
    response_model=WishlistRead,
    summary="Remove a product from the wishlist (idempotent)",
)
async def remove_from_wishlist(
    product_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> WishlistRead:
    return await WishlistService(session).remove(user.id, product_id)


@router.post(
    "/{product_id}/move-to-cart",
    response_model=CartRead,
    summary="Add the chosen variant to cart; optionally remove from wishlist",
)
async def move_to_cart(
    product_id: uuid.UUID,
    body: MoveToCartRequest,
    user: CurrentUser,
    session: DbSession,
) -> CartRead:
    return await WishlistService(session).move_to_cart(
        user_id=user.id,
        product_id=product_id,
        variant_id=body.variant_id,
        quantity=body.quantity,
        remove_from_wishlist=body.remove_from_wishlist,
    )
