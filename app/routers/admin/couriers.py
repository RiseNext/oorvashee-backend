"""Admin courier-access management — grant / revoke / list the courier role.

Router-level `require_role("admin")` gates every route (mirrors the other admin
routers), so this is ADMIN-ONLY: a courier or customer token → 403, no token →
401. A courier cannot reach these endpoints, so couriers can never grant/revoke
roles.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.admin_courier import CourierEmailRequest, CourierUser
from app.services.admin_courier_service import AdminCourierService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get(
    "",
    response_model=list[CourierUser],
    summary="List users who currently have courier access",
)
async def list_couriers(
    session: DbSession,
    _admin: CurrentAdmin,
) -> list[CourierUser]:
    return await AdminCourierService(session).list_couriers()


@router.post(
    "/grant",
    response_model=CourierUser,
    summary="Grant courier access to a signed-up user by email",
)
async def grant_courier(
    request: Request,
    body: CourierEmailRequest,
    session: DbSession,
    admin: CurrentAdmin,
) -> CourierUser:
    return await AdminCourierService(session).grant(
        str(body.email), actor_user_id=admin.id, request_id=_request_id(request)
    )


@router.post(
    "/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke courier access by email (reverts the user to customer)",
)
async def revoke_courier(
    request: Request,
    body: CourierEmailRequest,
    session: DbSession,
    admin: CurrentAdmin,
) -> None:
    await AdminCourierService(session).revoke(
        str(body.email), actor_user_id=admin.id, request_id=_request_id(request)
    )
