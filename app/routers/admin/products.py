"""Admin product CRUD + lifecycle + variant management.

All routes require admin role (router-level `require_role("admin")` gate).
Every mutation is attributed via `created_by` / `updated_by` (AuditableMixin)
and additionally writes one row to `audit_logs`.

Status transitions go through dedicated endpoints, NOT the generic PATCH —
keeps the state-machine in one place (AdminProductService).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.pagination import OffsetParams, Page, offset_params
from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.enums import ProductStatus
from app.models.user import User
from app.schemas.admin_product import (
    AdminCategoryAssignRequest,
    AdminProductCreate,
    AdminProductListItem,
    AdminProductRead,
    AdminProductUpdate,
    AdminVariantCreate,
    AdminVariantRead,
    AdminVariantUpdate,
)
from app.services.admin_product_service import AdminProductService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# =============================================================================
# Reads
# =============================================================================


@router.get(
    "",
    response_model=Page[AdminProductListItem],
    summary="List products (admin view — includes DRAFT / ARCHIVED)",
)
async def list_products(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_: Annotated[list[ProductStatus] | None, Query(alias="status")] = None,
    category_slug: Annotated[str | None, Query(max_length=220)] = None,
    featured_only: Annotated[bool, Query()] = False,
) -> Page[AdminProductListItem]:
    return await AdminProductService(session).list_products(
        params=params,
        q=q,
        statuses=status_,
        category_slug=category_slug,
        featured_only=featured_only,
    )


@router.get(
    "/{product_id}",
    response_model=AdminProductRead,
    summary="Get full admin detail for a product",
)
async def get_product(
    session: DbSession,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).get_product(product_id)


# =============================================================================
# Create / Update
# =============================================================================


@router.post(
    "",
    response_model=AdminProductRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product (status defaults to DRAFT)",
)
async def create_product(
    request: Request,
    body: AdminProductCreate,
    session: DbSession,
    admin: CurrentAdmin,
) -> AdminProductRead:
    return await AdminProductService(session).create_product(
        body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.patch(
    "/{product_id}",
    response_model=AdminProductRead,
    summary="Update product fields (slug + status are NOT changeable here)",
)
async def update_product(
    request: Request,
    body: AdminProductUpdate,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).update_product(
        product_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


# =============================================================================
# Lifecycle transitions
# =============================================================================


@router.post(
    "/{product_id}/publish",
    response_model=AdminProductRead,
    summary="DRAFT/UNAVAILABLE → PUBLISHED. Requires at least one active variant.",
)
async def publish_product(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).publish(
        product_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{product_id}/unpublish",
    response_model=AdminProductRead,
    summary="PUBLISHED → DRAFT (preserves published_at for re-publish history)",
)
async def unpublish_product(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).unpublish(
        product_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{product_id}/archive",
    response_model=AdminProductRead,
    summary="ANY → ARCHIVED (slug stays reserved; URL returns available=false)",
)
async def archive_product(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).archive(
        product_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{product_id}/unarchive",
    response_model=AdminProductRead,
    summary="ARCHIVED → DRAFT (deliberate two-step before re-publish)",
)
async def unarchive_product(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).unarchive(
        product_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


# =============================================================================
# Categories
# =============================================================================


@router.put(
    "/{product_id}/categories",
    response_model=AdminProductRead,
    summary="Replace the product's category set (idempotent under retry)",
)
async def set_categories(
    request: Request,
    body: AdminCategoryAssignRequest,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminProductRead:
    return await AdminProductService(session).set_categories(
        product_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


# =============================================================================
# Variants
# =============================================================================


@router.post(
    "/{product_id}/variants",
    response_model=AdminVariantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a variant + seed its inventory row in one transaction",
)
async def add_variant(
    request: Request,
    body: AdminVariantCreate,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> AdminVariantRead:
    return await AdminProductService(session).add_variant(
        product_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.patch(
    "/{product_id}/variants/{variant_id}",
    response_model=AdminVariantRead,
    summary="Update a variant. Stock changes go through inventory endpoints (Phase 3C).",
)
async def update_variant(
    request: Request,
    body: AdminVariantUpdate,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    variant_id: Annotated[uuid.UUID, Path()],
) -> AdminVariantRead:
    return await AdminProductService(session).update_variant(
        product_id, variant_id, body,
        actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.delete(
    "/{product_id}/variants/{variant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-deactivate a variant (preserves order history)",
)
async def deactivate_variant(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    variant_id: Annotated[uuid.UUID, Path()],
) -> None:
    await AdminProductService(session).deactivate_variant(
        product_id, variant_id,
        actor_user_id=admin.id, request_id=_request_id(request),
    )
