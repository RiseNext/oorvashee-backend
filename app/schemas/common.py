"""Shared response shapes."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class HealthResponse(BaseModel):
    status: str
    db: str | None = None
    version: str
    commit: str | None = None
    env: str


class LiveResponse(BaseModel):
    status: str = "ok"


class ErrorResponse(BaseModel):
    detail: str
    code: str
    request_id: str | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    total: int | None = Field(default=None, description="Optional total count")
