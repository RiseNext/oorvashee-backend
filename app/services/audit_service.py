"""AuditService — writes immutable rows to `audit_logs`.

Single entry point so every audit event has a consistent shape and we can
later swap the sink (e.g. dual-write to a SIEM) without touching call sites.

Never store PII in `metadata` (see audit_log.py docstring).
"""

from __future__ import annotations

import uuid
from typing import Any

from app.models.audit_log import AuditLog
from app.models.enums import AuditAction
from app.services.base import BaseService


class AuditService(BaseService):
    async def record(
        self,
        *,
        action: AuditAction,
        entity_type: str,
        entity_id: str | uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        row = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            actor_user_id=actor_user_id,
            summary=summary,
            metadata_=metadata or {},
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.session.add(row)
        await self.session.flush()
        return row
