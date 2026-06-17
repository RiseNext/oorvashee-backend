"""Admin video-wall management — list, create, edit, reorder, remove.

Router-level `require_role("admin")` gate. Admins paste a YouTube link, edit
copy / visibility, reorder the wall, and remove videos (soft delete). Every
mutation writes one `audit_logs` row.

Routes:
- GET    /admin/videos            list all (active + hidden)
- POST   /admin/videos            create (from a YouTube URL or id)
- PATCH  /admin/videos/{id}       update (url / title / link / visibility)
- PUT    /admin/videos/{id}/order set display_order
- DELETE /admin/videos/{id}       remove (soft delete)
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.video import (
    AdminVideoCreate,
    AdminVideoRead,
    AdminVideoUpdate,
    VideoReorderRequest,
)
from app.services.video_service import VideoService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.get(
    "",
    response_model=list[AdminVideoRead],
    summary="List all videos (includes hidden)",
)
async def list_videos(session: DbSession) -> list[AdminVideoRead]:
    return await VideoService(session).list_admin()


@router.post(
    "",
    response_model=AdminVideoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a video from a YouTube URL or id",
)
async def create_video(
    request: Request,
    body: AdminVideoCreate,
    session: DbSession,
    admin: CurrentAdmin,
) -> AdminVideoRead:
    return await VideoService(session).create(
        body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.get(
    "/{video_id}",
    response_model=AdminVideoRead,
    summary="Get video detail",
)
async def get_video(
    session: DbSession,
    video_id: Annotated[uuid.UUID, Path()],
) -> AdminVideoRead:
    return await VideoService(session).get_admin(video_id)


@router.patch(
    "/{video_id}",
    response_model=AdminVideoRead,
    summary="Update video link / title / shop link / visibility",
)
async def update_video(
    request: Request,
    body: AdminVideoUpdate,
    session: DbSession,
    admin: CurrentAdmin,
    video_id: Annotated[uuid.UUID, Path()],
) -> AdminVideoRead:
    return await VideoService(session).update(
        video_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.put(
    "/{video_id}/order",
    response_model=AdminVideoRead,
    summary="Set display_order (position in the wall)",
)
async def reorder_video(
    request: Request,
    body: VideoReorderRequest,
    session: DbSession,
    admin: CurrentAdmin,
    video_id: Annotated[uuid.UUID, Path()],
) -> AdminVideoRead:
    return await VideoService(session).reorder(
        video_id, body, actor_user_id=admin.id, request_id=_request_id(request),
    )


@router.delete(
    "/{video_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a video (soft delete)",
)
async def delete_video(
    request: Request,
    session: DbSession,
    admin: CurrentAdmin,
    video_id: Annotated[uuid.UUID, Path()],
) -> None:
    await VideoService(session).delete(
        video_id, actor_user_id=admin.id, request_id=_request_id(request),
    )
