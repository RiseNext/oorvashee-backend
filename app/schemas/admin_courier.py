"""Admin courier-access management schemas (admin-only).

Tiny surface: list current couriers, and grant / revoke by email. No payment or
order data here — this is purely RBAC management.
"""
from __future__ import annotations

from pydantic import BaseModel, EmailStr


class CourierUser(BaseModel):
    """A user currently holding the courier role (sourced from Clerk)."""

    id: str  # Clerk user id (e.g. "user_2abc…")
    email: str | None
    full_name: str | None


class CourierEmailRequest(BaseModel):
    """Body for grant + revoke — identifies the target user by email."""

    email: EmailStr
