"""Admin category CRUD + hierarchy + reorder.

Router-level `require_role("admin")` gate. Mutations attribute via
`AuditableMixin.created_by/updated_by` AND write one `audit_logs` row.

Routes:
- GET    /admin/categories            list (filters: q, kind[], is_active, parent_id)
- POST   /admin/categories            create
- GET    /admin/categories/{id}       detail
- PATCH  /admin/categories/{id}       update (slug + kind + is_active not accepted)
- POST   /admin/categories/{id}/archive
- POST   /admin/categories/{id}/unarchive
- PUT    /admin/categories/{id}/order set display_order
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.pagination import OffsetParams, Page, offset_params
from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.enums import CategoryKind
from app.models.user import User
from app.schemas.admin_category import (
    AdminCategoryCreate,
    AdminCategoryListItem,
    AdminCategoryRead,
    AdminCategoryUpdate,
    CategoryReorderRequest,
)
from app.services.admin_category_service import AdminCategoryService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# =============================================================================
# Reads
# =============================================================================


@router.get(
    "",
    response_model=Page[AdminCategoryListItem],
    summary="List categories (admin view — includes archived)",
)
async def list_categories(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    kind: Annotated[list[CategoryKind] | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    parent_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[AdminCategoryListItem]:
    return await AdminCategoryService(session).list_categories(
        params=params, q=q, kinds=kind, is_active=is_active, parent_id=parent_id,
    )


@router.get(
    "/{category_id}",
    response_model=AdminCategoryRead,
    summary="Get category detail",
)
async def get_category(
    session: DbSession,
    category_id: Annotated[uuid.UUID, Path()],
) -> AdminCategoryRead:
    return await AdminCategoryService(session).get_category(category_id)


# =============================================================================
# Create / Update
# =============================================================================


@router.post(
    "",
    response_model=AdminCategoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a category (slug auto-derived; kind is permanent)",
)
async def create_category(
    request: Request,
    body: AdminCategoryCreate,
    session: DbSession,
    admin: CurrentAdmin,
) -> AdminCategoryRead:
    return await AdminCategoryService(session).create_category(
        body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.patch(
    "/{category_id}",
    response_model=AdminCategoryRead,
    summary="Update category fields (slug, kind, is_active NOT changeable here)",
)
async def update_category(
    request: Request,
    body: AdminCategoryUpdate,
    session: DbSession,
    admin: CurrentAdmin,
    category_id: Annotated[uuid.UUID, Path()],
) -> AdminCategoryRead:
    return await AdminCategoryService(session).update_category(
        category_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


# =============================================================================
# Archive / unarchive (clean audit trail via STATUS_CHANGED)
# =============================================================================


@router.post(
    "/{category_id}/archive",
    response_model=AdminCategoryRead,
    summary="Mark inactive — hides from catalog filters; admin-only",
)
async def archive_category(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    category_id: Annotated[uuid.UUID, Path()],
) -> AdminCategoryRead:
    return await AdminCategoryService(session).archive(
        category_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{category_id}/unarchive",
    response_model=AdminCategoryRead,
    summary="Restore an archived category to active",
)
async def unarchive_category(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    category_id: Annotated[uuid.UUID, Path()],
) -> AdminCategoryRead:
    return await AdminCategoryService(session).unarchive(
        category_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


# =============================================================================
# Reorder
# =============================================================================


@router.put(
    "/{category_id}/order",
    response_model=AdminCategoryRead,
    summary="Set display_order. Bulk reorder ships later if admin demands it.",
)
async def reorder_category(
    request: Request,
    body: CategoryReorderRequest,
    session: DbSession,
    admin: CurrentAdmin,
    category_id: Annotated[uuid.UUID, Path()],
) -> AdminCategoryRead:
    return await AdminCategoryService(session).reorder(
        category_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )
