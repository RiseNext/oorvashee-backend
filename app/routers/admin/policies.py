"""Admin store-policy management — list, detail, edit.

Router-level `require_role("admin")` gate. The doc set is fixed (seeded by
migration), so there is no create/delete: admins edit copy, toggle visibility,
and reorder. Every mutation writes one `audit_logs` row.

Routes:
- GET   /admin/policies            list all (active + hidden)
- GET   /admin/policies/{id}       detail
- PATCH /admin/policies/{id}       update (key is immutable)
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.policy import AdminPolicyRead, AdminPolicyUpdate
from app.services.policy_service import PolicyService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get(
    "",
    response_model=list[AdminPolicyRead],
    summary="List all store policies (includes hidden)",
)
async def list_policies(session: DbSession) -> list[AdminPolicyRead]:
    return await PolicyService(session).list_admin()


@router.get(
    "/{policy_id}",
    response_model=AdminPolicyRead,
    summary="Get policy detail",
)
async def get_policy(
    session: DbSession,
    policy_id: Annotated[uuid.UUID, Path()],
) -> AdminPolicyRead:
    return await PolicyService(session).get_admin(policy_id)


@router.patch(
    "/{policy_id}",
    response_model=AdminPolicyRead,
    summary="Update policy copy / visibility / order (key NOT changeable)",
)
async def update_policy(
    request: Request,
    body: AdminPolicyUpdate,
    session: DbSession,
    admin: CurrentAdmin,
    policy_id: Annotated[uuid.UUID, Path()],
) -> AdminPolicyRead:
    return await PolicyService(session).update(
        policy_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )
