"""Admin media endpoints.

All routes here require admin role via `require_role("admin")`. Add other
roles (e.g. `merchandiser`) by extending the dependency list — the JWT
claim drives the check; no code changes elsewhere needed.

Routes:
- POST /admin/media/sign — request a Cloudinary upload signature
- POST /admin/products/{product_id}/images — attach an uploaded image
- DELETE /admin/products/{product_id}/images/{image_id} — remove an image
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status

from app.core.security import Principal, get_current_user, require_role
from app.db.deps import DbSession
from app.models.user import User
from app.schemas.media import (
    AttachProductImageRequest,
    ProductImageRead,
    UploadSignRequest,
    UploadSignResponse,
)
from app.services.media_service import MediaService

router = APIRouter(dependencies=[Depends(require_role("admin"))])

# Used to resolve the local `users` row so we can attribute writes; the
# require_role guard above has already enforced admin status.
CurrentAdmin = Annotated[User, Depends(get_current_user)]


@router.post(
    "/media/sign",
    response_model=UploadSignResponse,
    summary="Request a Cloudinary upload signature for a given context",
)
async def sign_upload(
    body: UploadSignRequest,
    session: DbSession,
    _admin: CurrentAdmin,
    _principal: Annotated[Principal, Depends(require_role("admin"))],
) -> UploadSignResponse:
    return await MediaService(session).create_upload_signature(body)


@router.post(
    "/products/{product_id}/images",
    response_model=ProductImageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a Cloudinary-uploaded image to a product (signature verified)",
)
async def attach_product_image(
    body: AttachProductImageRequest,
    session: DbSession,
    admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
) -> ProductImageRead:
    return await MediaService(session).attach_product_image(
        product_id=product_id,
        req=body,
        actor_user_id=admin.id,
    )


@router.delete(
    "/products/{product_id}/images/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a product image (also destroys the Cloudinary asset)",
)
async def delete_product_image(
    session: DbSession,
    _admin: CurrentAdmin,
    product_id: Annotated[uuid.UUID, Path()],
    image_id: Annotated[uuid.UUID, Path()],
) -> None:
    await MediaService(session).delete_product_image(
        product_id=product_id, image_id=image_id
    )
