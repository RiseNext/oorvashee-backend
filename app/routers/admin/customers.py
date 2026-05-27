"""Admin customer endpoints — CRM views.

Router-level `require_role("admin")` gate. The only mutation here is
`add_note` (writes `audit_logs`) — real customer mutations flow through
the Clerk webhook in `UserSyncService`.

Routes:
- GET    /admin/customers                       list (q, has_orders, sort, include_deleted)
- GET    /admin/customers/summary               segment counts
- GET    /admin/customers/{user_id}             detail (profile + addresses + roles +
                                                 KPI + recent orders)
- GET    /admin/customers/{user_id}/orders      paginated order history
- GET    /admin/customers/{user_id}/activity    timeline
- POST   /admin/customers/{user_id}/notes       admin note (audited)
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status

from app.core.pagination import OffsetParams, Page, offset_params
from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.admin_customer import (
    AddCustomerNoteRequest,
    AdminCustomerDetail,
    AdminCustomerListItem,
    CustomerActivityEvent,
    CustomerOrderSummary,
    CustomerSegmentSummary,
)
from app.services.admin_customer_service import AdminCustomerService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# =============================================================================
# Reads
# =============================================================================


@router.get(
    "",
    response_model=Page[AdminCustomerListItem],
    summary="List customers (admin view — with order aggregates)",
)
async def list_customers(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    has_orders: Annotated[bool | None, Query()] = None,
    sort: Annotated[
        str,
        Query(pattern=r"^(newest|oldest|ltv_desc|orders_desc|last_order_desc)$"),
    ] = "newest",
    include_deleted: Annotated[bool, Query()] = False,
) -> Page[AdminCustomerListItem]:
    return await AdminCustomerService(session).list_customers(
        params=params,
        q=q,
        has_orders=has_orders,
        sort=sort,
        include_deleted=include_deleted,
    )


@router.get(
    "/summary",
    response_model=CustomerSegmentSummary,
    summary="Customer segment counts — dashboard tile",
)
async def segment_summary(session: DbSession) -> CustomerSegmentSummary:
    return await AdminCustomerService(session).segment_summary()


@router.get(
    "/{user_id}",
    response_model=AdminCustomerDetail,
    summary="Customer detail — profile + addresses + roles + KPI + recent orders",
)
async def get_customer(
    session: DbSession,
    user_id: Annotated[uuid.UUID, Path()],
) -> AdminCustomerDetail:
    return await AdminCustomerService(session).get_customer_detail(user_id)


@router.get(
    "/{user_id}/orders",
    response_model=Page[CustomerOrderSummary],
    summary="Paginated order history for one customer",
)
async def list_customer_orders(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    user_id: Annotated[uuid.UUID, Path()],
) -> Page[CustomerOrderSummary]:
    return await AdminCustomerService(session).list_customer_orders(
        user_id, params=params,
    )


@router.get(
    "/{user_id}/activity",
    response_model=list[CustomerActivityEvent],
    summary="Customer activity timeline (newest first)",
)
async def get_activity(
    session: DbSession,
    user_id: Annotated[uuid.UUID, Path()],
) -> list[CustomerActivityEvent]:
    return await AdminCustomerService(session).get_activity(user_id)


# =============================================================================
# Add admin note
# =============================================================================


@router.post(
    "/{user_id}/notes",
    response_model=CustomerActivityEvent,
    status_code=status.HTTP_201_CREATED,
    summary="Add an admin note to a customer (audited)",
)
async def add_note(
    request: Request,
    body: AddCustomerNoteRequest,
    session: DbSession,
    admin: CurrentAdmin,
    user_id: Annotated[uuid.UUID, Path()],
) -> CustomerActivityEvent:
    return await AdminCustomerService(session).add_note(
        user_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )
