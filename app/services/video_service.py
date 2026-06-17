"""VideoService — public reads + admin CRUD of the storefront video wall.

Admins add YouTube films (by URL or id), edit copy / visibility, reorder, and
remove them; the storefront renders the active set as an autoplaying reels
wall. Every admin mutation writes one `audit_logs` row, mirroring
PolicyService / AdminCategoryService. Removal is a soft delete (sets
`deleted_at`) so audit references stay resolvable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import NotFoundError, ValidationError
from app.core.validators import extract_youtube_id
from app.models.enums import AuditAction
from app.models.video import Video
from app.repositories.video_repo import VideoRepository
from app.schemas.video import (
    AdminVideoCreate,
    AdminVideoRead,
    AdminVideoUpdate,
    VideoRead,
    VideoReorderRequest,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService


class VideoService(BaseService):
    @property
    def videos(self) -> VideoRepository:
        return VideoRepository(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ======================================================================
    # Public reads
    # ======================================================================

    async def list_public(self) -> list[VideoRead]:
        rows = await self.videos.list_active()
        return [VideoRead.model_validate(v) for v in rows]

    # ======================================================================
    # Admin reads
    # ======================================================================

    async def list_admin(self) -> list[AdminVideoRead]:
        rows = await self.videos.list_all()
        return [AdminVideoRead.model_validate(v) for v in rows]

    async def get_admin(self, video_id: uuid.UUID) -> AdminVideoRead:
        video = await self._require_video(video_id)
        return AdminVideoRead.model_validate(video)

    # ======================================================================
    # Create
    # ======================================================================

    async def create(
        self,
        body: AdminVideoCreate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminVideoRead:
        youtube_id = extract_youtube_id(body.url)
        if youtube_id is None:
            raise ValidationError(
                "Could not read a YouTube video id from that link",
                code="invalid_youtube_url",
            )

        video = Video(
            youtube_id=youtube_id,
            title=body.title,
            link_url=body.link_url,
            display_order=body.display_order,
            is_active=body.is_active,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        self.session.add(video)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.CREATE,
            entity_type="video",
            entity_id=video.id,
            actor_user_id=actor_user_id,
            summary=f"Added video '{video.title or video.youtube_id}'",
            metadata={"youtube_id": video.youtube_id, "link_url": video.link_url},
            request_id=request_id,
        )
        await self.session.refresh(video)
        return AdminVideoRead.model_validate(video)

    # ======================================================================
    # Update
    # ======================================================================

    async def update(
        self,
        video_id: uuid.UUID,
        body: AdminVideoUpdate,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminVideoRead:
        video = await self._require_video(video_id)

        changes = body.model_dump(exclude_unset=True)
        if not changes:
            raise ValidationError("Update body has no fields to apply")

        # `url` maps onto the stored `youtube_id` after extraction.
        if "url" in changes:
            youtube_id = extract_youtube_id(changes.pop("url"))
            if youtube_id is None:
                raise ValidationError(
                    "Could not read a YouTube video id from that link",
                    code="invalid_youtube_url",
                )
            changes["youtube_id"] = youtube_id

        if not changes:
            raise ValidationError("Update body has no fields to apply")

        before = self._snapshot(video)
        for field, value in changes.items():
            setattr(video, field, value)
        video.updated_by = actor_user_id
        await self.session.flush()

        after = self._snapshot(video)
        diff = {
            k: {"from": before[k], "to": after[k]}
            for k in changes
            if before.get(k) != after.get(k)
        }
        if diff:
            await self.audit.record(
                action=AuditAction.UPDATE,
                entity_type="video",
                entity_id=video.id,
                actor_user_id=actor_user_id,
                summary=f"Updated video '{video.title or video.youtube_id}' "
                f"fields: {sorted(diff)}",
                metadata={"changed": diff},
                request_id=request_id,
            )

        await self.session.refresh(video)
        return AdminVideoRead.model_validate(video)

    # ======================================================================
    # Reorder
    # ======================================================================

    async def reorder(
        self,
        video_id: uuid.UUID,
        body: VideoReorderRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> AdminVideoRead:
        video = await self._require_video(video_id)
        if video.display_order == body.display_order:
            await self.session.refresh(video)
            return AdminVideoRead.model_validate(video)

        before = video.display_order
        video.display_order = body.display_order
        video.updated_by = actor_user_id
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="video",
            entity_id=video.id,
            actor_user_id=actor_user_id,
            summary=f"Reordered video '{video.title or video.youtube_id}' "
            f"({before} → {video.display_order})",
            metadata={"display_order": {"from": before, "to": video.display_order}},
            request_id=request_id,
        )
        await self.session.refresh(video)
        return AdminVideoRead.model_validate(video)

    # ======================================================================
    # Delete (soft)
    # ======================================================================

    async def delete(
        self,
        video_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        video = await self._require_video(video_id)
        video.deleted_at = datetime.now(UTC)
        video.updated_by = actor_user_id
        await self.session.flush()
        await self.audit.record(
            action=AuditAction.DELETE,
            entity_type="video",
            entity_id=video.id,
            actor_user_id=actor_user_id,
            summary=f"Removed video '{video.title or video.youtube_id}'",
            metadata={"youtube_id": video.youtube_id},
            request_id=request_id,
        )

    # ======================================================================
    # Internal
    # ======================================================================

    async def _require_video(self, video_id: uuid.UUID) -> Video:
        video = await self.session.get(Video, video_id)
        if video is None or video.deleted_at is not None:
            raise NotFoundError("Video not found")
        return video

    @staticmethod
    def _snapshot(video: Video) -> dict[str, Any]:
        return {
            "youtube_id": video.youtube_id,
            "title": video.title,
            "link_url": video.link_url,
            "is_active": video.is_active,
        }
