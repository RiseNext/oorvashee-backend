"""Public video-wall endpoint — active videos in display order.

Unauthenticated. The storefront `/video` page renders these as an autoplaying
portrait reels wall.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.db.deps import DbSession
from app.schemas.video import VideoRead
from app.services.video_service import VideoService

router = APIRouter()


@router.get(
    "",
    response_model=list[VideoRead],
    summary="List active videos (display order)",
)
async def list_videos(session: DbSession) -> list[VideoRead]:
    return await VideoService(session).list_public()
