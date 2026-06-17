"""Video repository — reads over the storefront video wall.

Small surface: the wall is short and fully admin-curated, so there is no
pagination. `Video` carries `SoftDeleteMixin`, so every query filters
`deleted_at IS NULL` explicitly (the base repo does not auto-filter). Writes go
through the ORM instance in the service.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.video import Video
from app.repositories.base import BaseRepository


class VideoRepository(BaseRepository[Video]):
    model = Video

    async def list_all(self) -> list[Video]:
        """Every non-deleted video, active or hidden — admin view."""
        stmt = (
            select(Video)
            .where(Video.deleted_at.is_(None))
            .order_by(Video.display_order, Video.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_active(self) -> list[Video]:
        """Active videos only, in display order — storefront view."""
        stmt = (
            select(Video)
            .where(Video.is_active.is_(True), Video.deleted_at.is_(None))
            .order_by(Video.display_order, Video.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
