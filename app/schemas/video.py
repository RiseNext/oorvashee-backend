"""Video schemas — public read + admin create/update/reorder.

The admin pastes a YouTube URL (or bare id) as `url`; the service extracts the
canonical 11-char id via `app.core.validators.extract_youtube_id` and stores
that. Visibility toggles inline on PATCH (`is_active`); ordering goes through
the dedicated reorder endpoint, mirroring the category pattern.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VideoRead(BaseModel):
    """Public storefront view — only what the reels wall renders."""

    model_config = ConfigDict(from_attributes=True)

    youtube_id: str
    title: str | None
    link_url: str | None


class AdminVideoRead(BaseModel):
    """Full admin view, including audit + visibility fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    youtube_id: str
    title: str | None
    link_url: str | None
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class AdminVideoCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(
        min_length=1,
        max_length=2048,
        description="A YouTube watch / youtu.be / Shorts / embed URL, or a bare "
        "11-char video id. The id is extracted and stored.",
    )
    title: str | None = Field(default=None, max_length=160)
    link_url: str | None = Field(default=None, max_length=2048)
    display_order: int = Field(default=0, ge=0, le=10_000)
    is_active: bool = True

    @field_validator("title", "link_url")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AdminVideoUpdate(BaseModel):
    """Partial update. `display_order` is absent — it goes via /order."""

    model_config = ConfigDict(extra="ignore")

    url: str | None = Field(default=None, min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=160)
    link_url: str | None = Field(default=None, max_length=2048)
    is_active: bool | None = None

    @field_validator("title", "link_url")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class VideoReorderRequest(BaseModel):
    """Single-row reorder — set this video's position in the wall."""

    model_config = ConfigDict(extra="ignore")

    display_order: int = Field(ge=0, le=10_000)
