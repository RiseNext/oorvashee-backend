"""Policy schemas — public read + admin read/update.

The doc set is fixed (seeded by migration); admins edit copy but never create
or delete rows, and `key` is immutable (storefront contract). So there is no
Create schema and the Update schema omits `key`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PolicyRead(BaseModel):
    """Public storefront view — lean, only what the UI renders."""

    model_config = ConfigDict(from_attributes=True)

    key: str
    title: str
    summary: str | None
    body: str


class AdminPolicyRead(BaseModel):
    """Full admin view, including audit + visibility fields."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    title: str
    summary: str | None
    body: str
    display_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None


class AdminPolicyUpdate(BaseModel):
    """Partial update. `key` is intentionally absent — it is immutable."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, min_length=2, max_length=120)
    summary: str | None = Field(default=None, max_length=400)
    body: str | None = Field(default=None, min_length=1)
    display_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None

    @field_validator("title", "body")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value else value
