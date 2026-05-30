"""Public store-policy endpoints — list + by-key.

Unauthenticated. The storefront renders these under every product, in the
cart, at checkout, and on the dedicated `/policies/*` pages.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path

from app.db.deps import DbSession
from app.schemas.policy import PolicyRead
from app.services.policy_service import PolicyService

router = APIRouter()


@router.get(
    "",
    response_model=list[PolicyRead],
    summary="List active store policies (display order)",
)
async def list_policies(session: DbSession) -> list[PolicyRead]:
    return await PolicyService(session).list_public()


@router.get(
    "/{key}",
    response_model=PolicyRead,
    summary="Get a store policy by key (privacy | shipping | refund | terms)",
)
async def get_policy(
    session: DbSession,
    key: Annotated[str, Path(min_length=1, max_length=40)],
) -> PolicyRead:
    return await PolicyService(session).get_public(key)
