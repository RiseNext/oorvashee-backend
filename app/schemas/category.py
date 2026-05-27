"""Category Pydantic schemas — split by role: Create / Update / Read."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CategoryKind


class CategoryBase(BaseModel):
    slug: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=160)
    kind: CategoryKind
    description: str | None = None
    image_url: str | None = None
    parent_id: uuid.UUID | None = None
    display_order: int = 0
    is_active: bool = True
    seo_title: str | None = None
    seo_description: str | None = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    image_url: str | None = None
    parent_id: uuid.UUID | None = None
    display_order: int | None = None
    is_active: bool | None = None
    seo_title: str | None = None
    seo_description: str | None = None


class CategoryRead(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class CategorySummary(BaseModel):
    """Lightweight shape used in product responses + filter widgets.

    Carries `description` so the storefront category page can render its
    subtitle/intro from a single source of truth (the category row) without
    any hardcoded frontend copy.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    kind: CategoryKind
    description: str | None = None


class CategoryGroup(BaseModel):
    """Categories grouped by kind for the catalog filter sidebar."""

    fabric: list[CategorySummary] = Field(default_factory=list)
    occasion: list[CategorySummary] = Field(default_factory=list)
    region: list[CategorySummary] = Field(default_factory=list)
    price_bracket: list[CategorySummary] = Field(default_factory=list)
    color: list[CategorySummary] = Field(default_factory=list)
    collection: list[CategorySummary] = Field(default_factory=list)
