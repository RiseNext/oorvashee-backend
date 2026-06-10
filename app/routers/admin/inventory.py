"""Admin inventory endpoints.

Router-level `require_role("admin")` gate. All mutations are row-locked,
audit-logged (both `stock_movements` and `audit_logs`), and trigger
parent-product availability recomputation.

Routes:
- GET    /admin/inventory                       list (filters: q, low_stock_only, out_of_stock_only)
- GET    /admin/inventory/health                summary aggregates
- GET    /admin/inventory/movements             global movement audit (paginated, filterable)
- GET    /admin/inventory/{variant_id}          variant detail (+ recent movements)
- GET    /admin/inventory/{variant_id}/movements per-variant audit timeline
- POST   /admin/inventory/{variant_id}/adjust   manual stock adjustment
- PATCH  /admin/inventory/{variant_id}/threshold update low_stock_threshold
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.pagination import OffsetParams, Page, offset_params
from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.enums import ReservationStatus, StockMovementReason
from app.models.user import User
from app.schemas.admin_inventory import (
    InventoryDetail,
    InventoryHealthSummary,
    InventoryListItem,
    MovementItem,
    ReservationAdminItem,
    StockAdjustmentRequest,
    StockAdjustmentResult,
    ThresholdUpdateRequest,
)
from app.services.admin_inventory_service import AdminInventoryService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# =============================================================================
# Reads
# =============================================================================


@router.get(
    "",
    response_model=Page[InventoryListItem],
    summary="List inventory rows (paginated, filterable)",
)
async def list_inventory(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    low_stock_only: Annotated[bool, Query()] = False,
    out_of_stock_only: Annotated[bool, Query()] = False,
) -> Page[InventoryListItem]:
    return await AdminInventoryService(session).list_inventory(
        params=params,
        q=q,
        low_stock_only=low_stock_only,
        out_of_stock_only=out_of_stock_only,
    )


@router.get(
    "/health",
    response_model=InventoryHealthSummary,
    summary="One-glance inventory health (counts + totals across active variants)",
)
async def inventory_health(session: DbSession) -> InventoryHealthSummary:
    return await AdminInventoryService(session).health()


@router.get(
    "/movements",
    response_model=Page[MovementItem],
    summary="Global stock-movement audit timeline (newest first)",
)
async def list_movements(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    variant_id: Annotated[uuid.UUID | None, Query()] = None,
    reason: Annotated[list[StockMovementReason] | None, Query()] = None,
    actor_user_id: Annotated[uuid.UUID | None, Query()] = None,
    order_id: Annotated[uuid.UUID | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> Page[MovementItem]:
    return await AdminInventoryService(session).list_movements(
        params=params,
        variant_id=variant_id,
        reasons=reason,
        actor_user_id=actor_user_id,
        order_id=order_id,
        since=since,
        until=until,
    )


@router.get(
    "/reservations",
    response_model=Page[ReservationAdminItem],
    summary="Reservation lists (Active/Expired/Completed/Cancelled), paginated",
)
async def list_reservations(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    variant_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[list[ReservationStatus] | None, Query()] = None,
    session_id: Annotated[uuid.UUID | None, Query()] = None,
    user_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[ReservationAdminItem]:
    """Filter by `status` to render each tab: Active = reserved + payment_processing,
    Expired = expired, Completed = completed, Cancelled = cancelled."""
    return await AdminInventoryService(session).list_reservations(
        params=params,
        variant_id=variant_id,
        statuses=status,
        session_id=session_id,
        user_id=user_id,
    )


@router.get(
    "/{variant_id}",
    response_model=InventoryDetail,
    summary="Variant inventory detail + last 20 movements",
)
async def get_variant_inventory(
    session: DbSession,
    variant_id: Annotated[uuid.UUID, Path()],
) -> InventoryDetail:
    return await AdminInventoryService(session).get_variant_detail(variant_id)


@router.get(
    "/{variant_id}/movements",
    response_model=Page[MovementItem],
    summary="Per-variant stock-movement audit timeline",
)
async def list_variant_movements(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    variant_id: Annotated[uuid.UUID, Path()],
    reason: Annotated[list[StockMovementReason] | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> Page[MovementItem]:
    return await AdminInventoryService(session).list_movements(
        params=params,
        variant_id=variant_id,
        reasons=reason,
        actor_user_id=None,
        order_id=None,
        since=since,
        until=until,
    )


# =============================================================================
# Mutations
# =============================================================================


@router.post(
    "/{variant_id}/adjust",
    response_model=StockAdjustmentResult,
    status_code=status.HTTP_200_OK,
    summary="Manual stock adjustment (row-locked, reservation-aware, audited)",
)
async def adjust_stock(
    request: Request,
    body: StockAdjustmentRequest,
    session: DbSession,
    admin: CurrentAdmin,
    variant_id: Annotated[uuid.UUID, Path()],
) -> StockAdjustmentResult:
    return await AdminInventoryService(session).adjust(
        variant_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.patch(
    "/{variant_id}/threshold",
    response_model=InventoryDetail,
    summary="Update the variant's low-stock threshold",
)
async def update_threshold(
    request: Request,
    body: ThresholdUpdateRequest,
    session: DbSession,
    admin: CurrentAdmin,
    variant_id: Annotated[uuid.UUID, Path()],
) -> InventoryDetail:
    return await AdminInventoryService(session).set_low_stock_threshold(
        variant_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )
