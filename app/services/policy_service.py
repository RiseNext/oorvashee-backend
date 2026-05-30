"""PolicyService — public reads + admin edits of the store policy doc set.

The doc set is fixed (seeded by migration): admins edit copy / toggle
visibility / reorder, but never create or delete rows, and `key` is immutable.
Every admin mutation writes one `audit_logs` row, mirroring AdminCategoryService.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.exceptions import NotFoundError, ValidationError
from app.models.enums import AuditAction
from app.models.policy import Policy
from app.repositories.policy_repo import PolicyRepository
from app.schemas.policy import AdminPolicyRead, AdminPolicyUpdate, PolicyRead
from app.services.audit_service import AuditService
from app.services.base import BaseService


class PolicyService(BaseService):
    @property
    def policies(self) -> PolicyRepository:
        return PolicyRepository(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ======================================================================
    # Public reads
    # ======================================================================

    async def list_public(self) -> list[PolicyRead]:
        rows = await self.policies.list_active()
        return [PolicyRead.model_validate(p) for p in rows]

    async def get_public(self, key: str) -> PolicyRead:
        policy = await self.policies.get_by_key(key)
        if policy is None or not policy.is_active:
            raise NotFoundError("Policy not found")
        return PolicyRead.model_validate(policy)

    # ======================================================================
    # Admin reads
    # ======================================================================

    async def list_admin(self) -> list[AdminPolicyRead]:
        rows = await self.policies.list_all()
        return [AdminPolicyRead.model_validate(p) for p in rows]

    async def get_admin(self, policy_id: uuid.UUID) -> AdminPolicyRead:
        policy = await self._require_policy(policy_id)
        return AdminPolicyRead.model_validate(policy)

    # ======================================================================
    # Admin update
    # ======================================================================

    async def update(
        self,
        policy_id: uuid.UUID,
        body: AdminPolicyUpdate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminPolicyRead:
        policy = await self._require_policy(policy_id)

        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise ValidationError("Update body has no fields to apply")

        before = self._snapshot(policy)
        for field, value in changes.items():
            setattr(policy, field, value)
        policy.updated_by = actor_user_id
        await self.session.flush()

        after = self._snapshot(policy)
        diff = {
            k: {"from": before[k], "to": after[k]}
            for k in changes
            if before.get(k) != after.get(k)
        }
        if diff:
            await self.audit.record(
                action=AuditAction.UPDATE,
                entity_type="policy",
                entity_id=policy.id,
                actor_user_id=actor_user_id,
                summary=f"Updated policy '{policy.key}' fields: {sorted(diff)}",
                metadata={"changed": diff},
                request_id=request_id,
            )

        await self.session.refresh(policy)
        return AdminPolicyRead.model_validate(policy)

    # ======================================================================
    # Internal
    # ======================================================================

    async def _require_policy(self, policy_id: uuid.UUID) -> Policy:
        policy = await self.session.get(Policy, policy_id)
        if policy is None:
            raise NotFoundError("Policy not found")
        return policy

    @staticmethod
    def _snapshot(policy: Policy) -> dict[str, Any]:
        return {
            "title": policy.title,
            "summary": policy.summary,
            "body": policy.body,
            "display_order": policy.display_order,
            "is_active": policy.is_active,
        }
