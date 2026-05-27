"""Admin order endpoints — listing, detail, lifecycle, notes.

Router-level `require_role("admin")` gate. State transitions go through
dedicated endpoints (NOT a generic PATCH) so business rules + audit log
+ side effects (restock on cancel, COD auto-paid on delivery) stay in
one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.pagination import OffsetParams, Page, offset_params
from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus
from app.models.user import User
from app.schemas.admin_order import (
    AddOrderNoteRequest,
    AdminOrderDetail,
    AdminOrderListItem,
    CancelOrderRequest,
    OrderStatusSummary,
    OrderTimelineEvent,
    ShipmentUpdateRequest,
)
from app.services.admin_order_service import AdminOrderService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# =============================================================================
# Reads
# =============================================================================


@router.get(
    "",
    response_model=Page[AdminOrderListItem],
    summary="List orders (admin view — all statuses, all payment states)",
)
async def list_orders(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_: Annotated[list[OrderStatus] | None, Query(alias="status")] = None,
    payment_status: Annotated[list[PaymentStatus] | None, Query()] = None,
    payment_method: Annotated[PaymentMethod | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    has_shipment: Annotated[bool | None, Query()] = None,
    sort: Annotated[str, Query(pattern=r"^(newest|oldest|total_desc|total_asc)$")] = "newest",
) -> Page[AdminOrderListItem]:
    return await AdminOrderService(session).list_orders(
        params=params,
        q=q,
        statuses=status_,
        payment_statuses=payment_status,
        payment_method=payment_method,
        since=since,
        until=until,
        has_shipment=has_shipment,
        sort=sort,
    )


@router.get(
    "/summary",
    response_model=OrderStatusSummary,
    summary="One-glance order status + payment summary for the admin dashboard",
)
async def order_summary(session: DbSession) -> OrderStatusSummary:
    return await AdminOrderService(session).status_summary()


@router.get(
    "/{order_id}",
    response_model=AdminOrderDetail,
    summary="Get full order detail + timeline",
)
async def get_order(
    session: DbSession,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).get_order_detail(order_id)


# =============================================================================
# Lifecycle transitions
# =============================================================================


@router.post(
    "/{order_id}/packed",
    response_model=AdminOrderDetail,
    summary="placed → packed (requires payment captured for Razorpay)",
)
async def mark_packed(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).mark_packed(
        order_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{order_id}/shipped",
    response_model=AdminOrderDetail,
    summary="packed → shipped (requires courier_name + tracking_id)",
)
async def mark_shipped(
    request: Request,
    body: ShipmentUpdateRequest,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).mark_shipped(
        order_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{order_id}/delivered",
    response_model=AdminOrderDetail,
    summary="shipped → delivered (COD orders auto-flip payment_status → paid)",
)
async def mark_delivered(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).mark_delivered(
        order_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{order_id}/cancel",
    response_model=AdminOrderDetail,
    summary="Cancel a non-delivered order. Restocks inventory + flags refund need.",
)
async def cancel_order(
    request: Request,
    body: CancelOrderRequest,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).cancel_order(
        order_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{order_id}/cod-paid",
    response_model=AdminOrderDetail,
    summary="Record cash receipt on a COD order (cod_pending → paid)",
)
async def mark_cod_paid(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).mark_cod_paid(
        order_id, actor_user_id=admin.id, request_id=_request_id(request),
    )


# =============================================================================
# Shipment metadata + admin notes
# =============================================================================


@router.put(
    "/{order_id}/shipment",
    response_model=AdminOrderDetail,
    summary="Update courier/tracking on an existing shipment row",
)
async def update_shipment(
    request: Request,
    body: ShipmentUpdateRequest,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> AdminOrderDetail:
    return await AdminOrderService(session).update_shipment(
        order_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.post(
    "/{order_id}/notes",
    response_model=OrderTimelineEvent,
    status_code=status.HTTP_201_CREATED,
    summary="Add an admin note (separate from `order.notes` which is customer-facing)",
)
async def add_note(
    request: Request,
    body: AddOrderNoteRequest,
    session: DbSession,
    admin: CurrentAdmin,
    order_id: Annotated[uuid.UUID, Path()],
) -> OrderTimelineEvent:
    return await AdminOrderService(session).add_note(
        order_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )
