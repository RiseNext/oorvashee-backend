"""Admin category schemas.

Critical invariants (matching the AdminProduct pattern):
- `slug` is server-generated at creation; Update payload does NOT carry it.
  Category slugs drive the SEO URL pattern `/sarees/[category]/[slug]`
  (PRD §10) — changing it would break Google + bookmarks.
- `kind` is the filter-group axis (FABRIC vs OCCASION vs COLOR …) — also
  immutable. A `kind` change would mean the category jumps filter groups
  in the admin UI and existing frontend bookmarks would dangle. Re-create
  under the new kind instead.
- `is_active` toggles via dedicated archive/unarchive endpoints, not PATCH.

Future-proofing:
- `parent_id` supports nested categories. Cycle prevention + depth cap
  enforced by the service.
- Image fields (`image_url` + `image_public_id`) are a pair. PATCH replaces
  the pair atomically; the service queues a Cloudinary destroy for the
  old `public_id` when it changes.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import CategoryKind


class AdminCategoryCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=2, max_length=160)
    kind: CategoryKind
    slug: str | None = Field(
        default=None,
        min_length=2,
        max_length=160,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Optional override. Lowercase letters, digits, hyphens. "
        "Auto-generated from name if omitted.",
    )
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    image_public_id: str | None = Field(default=None, max_length=255)
    parent_id: uuid.UUID | None = None
    display_order: int = Field(default=0, ge=0, le=10_000)
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _image_pair_consistent(self) -> AdminCategoryCreate:
        # Both or neither — never a stranded URL pointing at no asset, never
        # a `public_id` we can't display.
        if (self.image_url is None) != (self.image_public_id is None):
            raise ValueError(
                "image_url and image_public_id must be provided together"
            )
        return self


class AdminCategoryUpdate(BaseModel):
    """Partial update. `slug`, `kind`, `is_active` intentionally absent.

    Status / visibility changes go through dedicated archive endpoints so
    the audit trail records a single 'STATUS_CHANGED' event instead of
    being buried inside a generic field-diff.
    """

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = None
    image_url: str | None = Field(default=None, max_length=2048)
    image_public_id: str | None = Field(default=None, max_length=255)
    parent_id: uuid.UUID | None = None
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = None

    # Clearing the image: send both fields as explicit null. The schema
    # uses `model_dump(exclude_unset=True)` semantics so unsent fields stay
    # untouched. To clear: client sends `"image_url": null, "image_public_id": null`.

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class CategoryReorderRequest(BaseModel):
    """Single-row reorder. Bulk reordering is Phase 3B+ if admin asks."""

    model_config = ConfigDict(extra="ignore")

    display_order: int = Field(ge=0, le=10_000)


class AdminCategoryRead(BaseModel):
    """Full admin view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    kind: CategoryKind
    description: str | None
    image_url: str | None
    image_public_id: str | None
    parent_id: uuid.UUID | None
    display_order: int
    is_active: bool
    seo_title: str | None
    seo_description: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class AdminCategoryListItem(BaseModel):
    """Lean shape for the admin category table — includes product counts."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    kind: CategoryKind
    parent_id: uuid.UUID | None
    display_order: int
    is_active: bool
    image_url: str | None
    product_count: int = Field(description="Total products linked, all statuses.")
    published_product_count: int = Field(
        description="Subset that are currently published (catalog-visible)."
    )
    updated_at: datetime
