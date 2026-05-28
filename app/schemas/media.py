"""Media schemas — signature request/response + post-upload attach payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MediaContext(StrEnum):
    """Where this asset lives + what constraints apply.

    Extending this enum is the public seam for adding future media types
    (reels, influencer content, WhatsApp catalog imagery). Each new context
    needs (a) an entry here, (b) a `ContextConfig` in `media_service.py`,
    (c) — optionally — a destination entity + attach endpoint.
    """

    PRODUCT = "product"
    CATEGORY = "category"
    BANNER = "banner"
    # Reserved for future:
    REEL = "reel"
    INFLUENCER = "influencer"
    WHATSAPP_CATALOG = "whatsapp_catalog"


# ---------- Sign request / response ----------


class UploadSignRequest(BaseModel):
    context: MediaContext
    entity_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Required for product / category / banner — gives the asset a "
            "scoped folder. Optional for reusable assets (reels, influencer)."
        ),
    )
    tags: list[str] = Field(default_factory=list, max_length=10)


class UploadSignResponse(BaseModel):
    """Everything the frontend hands directly to Cloudinary's `/upload`."""

    cloud_name: str
    api_key: str
    timestamp: int
    signature: str
    upload_url: str
    folder: str
    eager: list[str]
    allowed_formats: list[str]
    max_file_size: int
    resource_type: str
    public_id: str | None
    tags: list[str]
    # Pre-formed Cloudinary contextual-metadata value (`key=value|key=value`),
    # transmitted to Cloudinary verbatim (matches the signed value exactly).
    context: str
    # Informational — TTL the signature stays valid (Cloudinary uses ~1h
    # by default; we surface it so the frontend can refresh proactively).
    expires_in_seconds: int


# ---------- Cloudinary upload response (verified before insert) ----------


class CloudinaryUploadResult(BaseModel):
    """Subset of Cloudinary's upload response we read + verify.

    `extra='ignore'` so future Cloudinary fields don't break parsing.
    Field names match Cloudinary's payload verbatim.
    """

    model_config = ConfigDict(extra="ignore")

    public_id: str
    version: int
    signature: str
    secure_url: str
    url: str | None = None  # http variant; we always store secure_url
    format: str | None = None
    resource_type: Literal["image", "video", "raw", "auto"] = "image"
    bytes: int | None = None
    width: int | None = None
    height: int | None = None
    folder: str | None = None


# ---------- Attach payloads (per entity type) ----------


class AttachProductImageRequest(BaseModel):
    """Frontend POSTs this after Cloudinary upload succeeds."""

    cloudinary_response: CloudinaryUploadResult
    alt_text: str | None = Field(default=None, max_length=255)
    position: int | None = Field(default=None, ge=0, le=99)
    is_primary: bool = False


class ProductImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    cloudinary_public_id: str
    url: str
    alt_text: str | None
    width: int | None
    height: int | None
    format: str | None
    bytes: int | None
    position: int
    is_primary: bool
    created_at: datetime
