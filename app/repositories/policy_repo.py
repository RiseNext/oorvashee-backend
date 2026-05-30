"""Policy repository — reads over the fixed store-policy doc set.

Tiny surface: the set is small and fixed, so there is no pagination. Writes
go through the ORM instance in the service (BaseRepository handles refresh).
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.policy import Policy
from app.repositories.base import BaseRepository


class PolicyRepository(BaseRepository[Policy]):
    model = Policy

    async def list_all(self) -> list[Policy]:
        """Every doc, active or not — admin view."""
        stmt = select(Policy).order_by(Policy.display_order, Policy.title)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active(self) -> list[Policy]:
        """Active docs only, in display order — storefront view."""
        stmt = (
            select(Policy)
            .where(Policy.is_active.is_(True))
            .order_by(Policy.display_order, Policy.title)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_key(self, key: str) -> Policy | None:
        stmt = select(Policy).where(Policy.key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
