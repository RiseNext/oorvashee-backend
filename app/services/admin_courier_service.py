"""AdminCourierService — grant / revoke / list the courier role (admin-only).

Source of truth is Clerk `public_metadata.role` (what the JWT carries and
`require_role` reads). This service writes that field via ClerkManagementClient
and records each grant/revoke in the audit log.

Single-role safety: because a user has ONE role value, granting courier to an
admin would DEMOTE them and revoking would wipe a non-courier's role. Both are
guarded against here.
"""
from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.integrations.clerk_management import ClerkManagementClient, ClerkUserLite
from app.models.enums import AuditAction
from app.schemas.admin_courier import CourierUser
from app.services.audit_service import AuditService
from app.services.base import BaseService

_COURIER = "courier"
_ADMIN = "admin"


class AdminCourierService(BaseService):
    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    @property
    def _clerk(self) -> ClerkManagementClient:
        return ClerkManagementClient(get_settings())

    async def list_couriers(self) -> list[CourierUser]:
        users = await self._clerk.list_users_with_role(_COURIER)
        return [self._to_schema(u) for u in users]

    async def grant(
        self, email: str, *, actor_user_id: uuid.UUID, request_id: str | None = None
    ) -> CourierUser:
        user = await self._clerk.find_user_by_email(email)
        if user is None:
            raise NotFoundError(
                "No signed-up user with that email. Ask them to sign up first.",
                code="user_not_found",
            )
        if user.role == _COURIER:
            raise ConflictError("That user already has courier access.", code="already_courier")
        if user.role == _ADMIN:
            # Never silently demote an admin (role is single-valued).
            raise ConflictError(
                "That user is an admin — change their role in Clerk if intended.",
                code="is_admin",
            )

        await self._clerk.set_user_role(user.id, _COURIER)
        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="user_role",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            summary="courier_role_granted",
            metadata={"role": _COURIER, "op": "grant"},
            request_id=request_id,
        )
        # Reflect the new role in the returned object.
        return CourierUser(id=user.id, email=user.email, full_name=user.full_name)

    async def revoke(
        self, email: str, *, actor_user_id: uuid.UUID, request_id: str | None = None
    ) -> None:
        user = await self._clerk.find_user_by_email(email)
        if user is None:
            raise NotFoundError("No user with that email.", code="user_not_found")
        if user.role != _COURIER:
            # Only ever clears the courier role — can't wipe an admin/customer.
            raise ConflictError("That user is not a courier.", code="not_a_courier")

        await self._clerk.set_user_role(user.id, None)  # → defaults back to "customer"
        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="user_role",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            summary="courier_role_revoked",
            metadata={"op": "revoke"},
            request_id=request_id,
        )

    @staticmethod
    def _to_schema(u: ClerkUserLite) -> CourierUser:
        return CourierUser(id=u.id, email=u.email, full_name=u.full_name)
