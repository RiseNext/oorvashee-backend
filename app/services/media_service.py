"""MediaService — single owner of Cloudinary upload signing + asset registration.

Architecture:
- Backend signs an upload envelope with per-context constraints (folder, max
  size, allowed formats, eager transformations). Cloudinary enforces them.
- Frontend uploads directly to Cloudinary (no bytes through Railway).
- Frontend POSTs the Cloudinary response back; we VERIFY the signature
  before persisting any metadata.

Per-context configuration is a module-level dict so adding a new media type
is two changes: extend `MediaContext` + add a `CONTEXT_CONFIGS` entry. No
class refactor required.

Eager transformations:
- Pre-generated at upload time so the CDN cache is warm on first read.
- Format `f_auto` lets Cloudinary serve AVIF/WebP/JPEG based on client
  Accept headers — modern browsers get AVIF for free.
- Quality `q_auto` lets Cloudinary pick the optimal compression per format.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.integrations.cloudinary_client import CloudinaryClient
from app.models.product import Product
from app.models.product_image import ProductImage
from app.schemas.media import (
    AttachProductImageRequest,
    CloudinaryUploadResult,
    MediaContext,
    ProductImageRead,
    UploadSignRequest,
    UploadSignResponse,
)
from app.services.base import BaseService

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Per-context configuration — the public seam for new media types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ContextConfig:
    """Constraints + transformations baked into the upload signature.

    `folder_template` uses `{entity_id}` for entity-scoped contexts;
    `{shared}` placeholder for context-wide buckets (reels, influencer).
    """

    folder_template: str
    resource_type: str  # "image" | "video"
    max_bytes: int
    allowed_formats: list[str]
    eager: list[str]
    requires_entity_id: bool = True


# Eager transformations — pre-generated variants for fast first delivery.
# Convention:
#   - All use f_auto for browser-negotiated format (AVIF → WebP → JPEG).
#   - q_auto picks compression per-format.
#   - c_fill crops to exact dimensions (consistent UI grid).
#   - DPR variants come for free via responsive `srcset` on the frontend.

_PRODUCT_EAGER = [
    "c_fill,w_320,h_400,q_auto,f_auto",   # card thumbnail
    "c_fill,w_640,h_800,q_auto,f_auto",   # card hi-dpi
    "c_fill,w_1280,h_1600,q_auto,f_auto", # PDP main
]
_BANNER_EAGER = [
    "c_fill,w_1920,h_720,q_auto,f_auto",  # desktop hero
    "c_fill,w_1280,h_540,q_auto,f_auto",  # tablet
    "c_fill,w_768,h_1024,q_auto,f_auto",  # mobile (taller aspect)
]
_CATEGORY_EAGER = [
    "c_fill,w_480,h_600,q_auto,f_auto",   # collection card
    "c_fill,w_1920,h_480,q_auto,f_auto",  # category banner strip
]

CONTEXT_CONFIGS: dict[MediaContext, ContextConfig] = {
    MediaContext.PRODUCT: ContextConfig(
        folder_template="products/{entity_id}",
        resource_type="image",
        max_bytes=30 * 1024 * 1024,
        allowed_formats=["jpg", "jpeg", "png", "webp", "avif"],
        eager=_PRODUCT_EAGER,
    ),
    MediaContext.CATEGORY: ContextConfig(
        folder_template="categories/{entity_id}",
        resource_type="image",
        max_bytes=15 * 1024 * 1024,
        allowed_formats=["jpg", "jpeg", "png", "webp", "avif"],
        eager=_CATEGORY_EAGER,
    ),
    MediaContext.BANNER: ContextConfig(
        folder_template="banners/{entity_id}",
        resource_type="image",
        max_bytes=20 * 1024 * 1024,
        allowed_formats=["jpg", "jpeg", "png", "webp", "avif"],
        eager=_BANNER_EAGER,
    ),
    # Future: video contexts will use resource_type="video" + larger
    # max_bytes + video-specific eager (h264 + hls). Keep the same shape.
    MediaContext.REEL: ContextConfig(
        folder_template="reels/{shared}",
        resource_type="video",
        max_bytes=200 * 1024 * 1024,
        allowed_formats=["mp4", "mov", "webm"],
        eager=[],  # video eager is heavier; defer until reels ship
        requires_entity_id=False,
    ),
    MediaContext.INFLUENCER: ContextConfig(
        folder_template="influencer/{shared}",
        resource_type="image",
        max_bytes=20 * 1024 * 1024,
        allowed_formats=["jpg", "jpeg", "png", "webp", "avif"],
        eager=[
            "c_fill,w_1080,h_1080,q_auto,f_auto",  # square
            "c_fill,w_1080,h_1920,q_auto,f_auto",  # vertical (stories)
        ],
        requires_entity_id=False,
    ),
    MediaContext.WHATSAPP_CATALOG: ContextConfig(
        # WhatsApp commerce catalog: square crop, ≤5 MB per Meta spec.
        folder_template="whatsapp/{shared}",
        resource_type="image",
        max_bytes=5 * 1024 * 1024,
        allowed_formats=["jpg", "jpeg", "png"],
        eager=["c_fill,w_1024,h_1024,q_auto,f_jpg"],
        requires_entity_id=False,
    ),
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MediaService(BaseService):
    def _client(self) -> CloudinaryClient:
        return CloudinaryClient(get_settings())

    def _settings(self) -> Settings:
        return get_settings()

    # ------------------------------------------------------------------
    # Sign
    # ------------------------------------------------------------------

    async def create_upload_signature(
        self, req: UploadSignRequest
    ) -> UploadSignResponse:
        config = CONTEXT_CONFIGS.get(req.context)
        if config is None:  # defensive — enum guards this but keep an explicit branch
            raise ValidationError(
                f"Unknown media context: {req.context}",
                code="unknown_media_context",
            )

        if config.requires_entity_id and req.entity_id is None:
            raise ValidationError(
                f"entity_id is required for context '{req.context.value}'",
                code="entity_id_required",
            )

        # Validate the entity actually exists when one is required.
        if req.entity_id is not None and req.context == MediaContext.PRODUCT:
            await self._require_product(req.entity_id)
        # (Banner / category existence checks will live with the admin CRUD
        # endpoints in Phase 3.)

        settings = self._settings()
        folder_segment = (
            str(req.entity_id) if req.entity_id else "shared"
        )
        folder = (
            f"{settings.cloudinary_upload_folder}/"
            + config.folder_template.format(
                entity_id=folder_segment, shared=folder_segment
            )
        )

        # Only stable, deterministic fields are signed (timestamp/folder/eager/
        # public_id). Metadata constraints (allowed_formats/max_file_size/tags/
        # context) are intentionally NOT signed or sent — they caused
        # Cloudinary canonicalisation mismatches; the signed `folder` already
        # scopes the asset.
        envelope = self._client().build_signed_envelope(
            folder=folder,
            eager=config.eager,
            resource_type=config.resource_type,
        )

        log.info(
            "media_signature_issued",
            context=req.context.value,
            entity_id=str(req.entity_id) if req.entity_id else None,
            folder=folder,
            resource_type=config.resource_type,
        )

        return UploadSignResponse(
            cloud_name=envelope.cloud_name,
            api_key=envelope.api_key,
            timestamp=envelope.timestamp,
            signature=envelope.signature,
            upload_url=envelope.upload_url,
            folder=envelope.folder,
            eager=envelope.eager,
            resource_type=envelope.resource_type,
            public_id=envelope.public_id,
            expires_in_seconds=settings.cloudinary_signature_ttl_seconds,
        )

    # ------------------------------------------------------------------
    # Attach: product image
    # ------------------------------------------------------------------

    async def attach_product_image(
        self,
        *,
        product_id: uuid.UUID,
        req: AttachProductImageRequest,
        actor_user_id: uuid.UUID | None,
    ) -> ProductImageRead:
        """Verify Cloudinary signature, then INSERT a product_images row."""
        await self._require_product(product_id)

        payload = req.cloudinary_response
        self._verify_payload_belongs_to_product(payload, product_id)

        # Critical: verify Cloudinary actually signed this response.
        self._client().verify_response(payload.model_dump())

        position = await self._next_position(product_id, override=req.position)

        # First image becomes primary by default (frontend convenience).
        is_primary = req.is_primary
        if not is_primary:
            existing_primary = await self.session.execute(
                select(func.count())
                .select_from(ProductImage)
                .where(
                    ProductImage.product_id == product_id,
                    ProductImage.is_primary.is_(True),
                )
            )
            if int(existing_primary.scalar_one()) == 0:
                is_primary = True

        # If caller explicitly set primary, unset any existing one.
        if req.is_primary:
            await self.session.execute(
                ProductImage.__table__.update()
                .where(ProductImage.product_id == product_id)
                .values(is_primary=False)
            )

        image = ProductImage(
            product_id=product_id,
            cloudinary_public_id=payload.public_id,
            url=payload.secure_url,
            alt_text=req.alt_text,
            width=payload.width,
            height=payload.height,
            format=payload.format,
            bytes=payload.bytes,
            position=position,
            is_primary=is_primary,
        )
        self.session.add(image)
        await self.session.flush()

        log.info(
            "product_image_attached",
            product_id=str(product_id),
            image_id=str(image.id),
            cloudinary_public_id=payload.public_id,
            actor=str(actor_user_id) if actor_user_id else None,
        )
        return ProductImageRead.model_validate(image)

    async def delete_product_image(
        self, *, product_id: uuid.UUID, image_id: uuid.UUID
    ) -> None:
        """Delete the row AND the Cloudinary asset (best-effort on the latter)."""
        stmt = select(ProductImage).where(
            ProductImage.id == image_id,
            ProductImage.product_id == product_id,
        )
        image = (await self.session.execute(stmt)).scalar_one_or_none()
        if image is None:
            raise NotFoundError("Product image not found")

        public_id = image.cloudinary_public_id
        was_primary = image.is_primary

        await self.session.delete(image)
        await self.session.flush()

        # If we removed the primary, promote the next-lowest-position survivor.
        if was_primary:
            next_image = (
                await self.session.execute(
                    select(ProductImage)
                    .where(ProductImage.product_id == product_id)
                    .order_by(ProductImage.position.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if next_image is not None:
                next_image.is_primary = True
                await self.session.flush()

        # Cloudinary deletion is intentionally non-blocking for the response —
        # storage costs are tiny and re-running a cleanup cron later catches
        # any orphans if this call fails.
        try:
            await self._client().destroy(public_id)
        except Exception:
            log.warning(
                "cloudinary_destroy_failed_continuing",
                public_id=public_id,
                product_id=str(product_id),
            )

        log.info(
            "product_image_deleted",
            product_id=str(product_id),
            image_id=str(image_id),
            cloudinary_public_id=public_id,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _require_product(self, product_id: uuid.UUID) -> Product:
        product = await self.session.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found")
        return product

    @staticmethod
    def _verify_payload_belongs_to_product(
        payload: CloudinaryUploadResult, product_id: uuid.UUID
    ) -> None:
        """Defence-in-depth: the signature is correct but the public_id should
        also live under `products/{product_id}/...`. Prevents an admin from
        accidentally (or maliciously) attaching a banner asset to a product.
        """
        expected_segment = f"products/{product_id}"
        if expected_segment not in payload.public_id:
            # 400 — the client sent a payload that doesn't match the
            # signed-folder constraint. Not an upstream failure.
            raise ValidationError(
                "Uploaded asset does not belong to this product folder",
                code="asset_folder_mismatch",
                extra={
                    "expected_segment": expected_segment,
                    "got_public_id": payload.public_id,
                },
            )

    async def _next_position(
        self, product_id: uuid.UUID, *, override: int | None
    ) -> int:
        if override is not None:
            return override
        result = await self.session.execute(
            select(func.coalesce(func.max(ProductImage.position), -1) + 1)
            .where(ProductImage.product_id == product_id)
        )
        return int(result.scalar_one())
