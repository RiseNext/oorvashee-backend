"""Clerk webhook payload schemas.

Clerk sends events wrapped by Svix; the outer event has `{type, data, ...}`
where `data` carries the resource. We model the user-resource shape and
derive primary email / phone / role via convenience properties so the
sync service doesn't have to re-implement the same lookups.

Field names match Clerk's snake_case payload exactly — no aliases needed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ClerkEmailAddress(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    email_address: str


class ClerkPhoneNumber(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    phone_number: str


class ClerkUser(BaseModel):
    """Subset of the Clerk user object we actually persist.

    Anything Clerk adds in future (verification flags, OAuth links,
    organisation memberships) is silently ignored thanks to
    `extra='ignore'`.
    """

    model_config = ConfigDict(extra="ignore")

    id: str  # this is the Clerk user id (e.g. "user_2abc...")
    email_addresses: list[ClerkEmailAddress] = []
    primary_email_address_id: str | None = None
    phone_numbers: list[ClerkPhoneNumber] = []
    primary_phone_number_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    image_url: str | None = None
    public_metadata: dict[str, Any] = {}

    # ---------- Convenience derivations ----------

    @property
    def primary_email(self) -> str | None:
        if not self.email_addresses:
            return None
        if self.primary_email_address_id:
            for addr in self.email_addresses:
                if addr.id == self.primary_email_address_id:
                    return addr.email_address
        # Fallback: first listed
        return self.email_addresses[0].email_address

    @property
    def primary_phone(self) -> str | None:
        if not self.phone_numbers:
            return None
        if self.primary_phone_number_id:
            for ph in self.phone_numbers:
                if ph.id == self.primary_phone_number_id:
                    return ph.phone_number
        return self.phone_numbers[0].phone_number

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) if parts else None

    @property
    def role(self) -> str:
        """Single role read from `public_metadata.role`. Defaults to `customer`."""
        raw = self.public_metadata.get("role")
        return str(raw).lower().strip() if raw else "customer"


class ClerkUserDeletedData(BaseModel):
    """Payload shape for `user.deleted`. Clerk only sends id + deleted flag."""

    model_config = ConfigDict(extra="ignore")

    id: str
    deleted: bool = True
