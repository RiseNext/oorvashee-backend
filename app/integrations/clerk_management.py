"""Thin async wrapper over the Clerk Backend (management) API.

Mirrors `razorpay_client`'s style — raw `httpx`, no SDK. Used to GRANT / REVOKE
the courier role by writing the user's `public_metadata.role`, which is the SAME
field the auth path reads (app/core/security.py::_principal_from_claims and
app/schemas/clerk_webhook.py::ClerkUser.role). Writing it here means the user's
NEXT Clerk JWT carries `role: "courier"`, so `require_role("courier")` passes;
removing it reverts them to the default "customer".

The Clerk secret key is server-side ONLY (never sent to the browser).

Endpoints used:
- GET   /v1/users?email_address=<email>   find a user by email
- GET   /v1/users?limit=&offset=          list users (we filter by metadata here)
- PATCH /v1/users/{user_id}/metadata      merge public_metadata.role
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger

log = get_logger(__name__)

CLERK_API_BASE = "https://api.clerk.com/v1"
CLERK_TIMEOUT_SECONDS = 10.0
_PAGE = 100
# Cap on users scanned when listing by role. Couriers are rare and this is a
# small store; raise (or add a metadata-indexed search) if the user base grows.
_MAX_SCAN = 1000


@dataclass(frozen=True, slots=True)
class ClerkUserLite:
    id: str
    email: str | None
    full_name: str | None
    role: str


def _role_of(public_metadata: dict[str, Any] | None) -> str:
    """Mirror the read path exactly (clerk_webhook.py::ClerkUser.role)."""
    raw = (public_metadata or {}).get("role")
    return str(raw).lower().strip() if raw else "customer"


def _primary_email(user: dict[str, Any]) -> str | None:
    emails = user.get("email_addresses") or []
    pid = user.get("primary_email_address_id")
    for e in emails:
        if e.get("id") == pid:
            return e.get("email_address")
    return emails[0].get("email_address") if emails else None


def _full_name(user: dict[str, Any]) -> str | None:
    parts = [p for p in (user.get("first_name"), user.get("last_name")) if p]
    return " ".join(parts) if parts else None


def _to_lite(user: dict[str, Any]) -> ClerkUserLite:
    return ClerkUserLite(
        id=str(user["id"]),
        email=_primary_email(user),
        full_name=_full_name(user),
        role=_role_of(user.get("public_metadata")),
    )


def _as_user_list(payload: Any) -> list[dict[str, Any]]:
    """Clerk's GET /v1/users returns a bare array; tolerate a {data:[...]} shape too."""
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, list) else []
    return payload if isinstance(payload, list) else []


class ClerkManagementClient:
    """Stateless Clerk Backend API client. Instantiate per service call; cheap."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.clerk_secret_key

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=CLERK_API_BASE,
            headers={"Authorization": f"Bearer {self._secret}"},
            timeout=CLERK_TIMEOUT_SECONDS,
        )

    async def find_user_by_email(self, email: str) -> ClerkUserLite | None:
        try:
            async with self._client() as client:
                resp = await client.get(
                    "/users", params={"email_address": email, "limit": 1}
                )
        except httpx.RequestError as exc:
            log.exception("clerk_user_lookup_error")
            raise IntegrationError("Clerk request failed", code="clerk_unreachable") from exc
        self._raise_for_status(resp, "clerk_user_lookup_failed")
        users = _as_user_list(resp.json())
        return _to_lite(users[0]) if users else None

    async def set_user_role(self, user_id: str, role: str | None) -> None:
        """Set public_metadata.role. `role=None` removes it (→ default customer).

        Clerk MERGES public_metadata, so only the `role` key is touched; other
        metadata the user may carry is preserved.
        """
        body = {"public_metadata": {"role": role}}
        try:
            async with self._client() as client:
                resp = await client.patch(f"/users/{user_id}/metadata", json=body)
        except httpx.RequestError as exc:
            log.exception("clerk_set_role_error")
            raise IntegrationError("Clerk request failed", code="clerk_unreachable") from exc
        self._raise_for_status(resp, "clerk_set_role_failed")

    async def list_users_with_role(self, role: str) -> list[ClerkUserLite]:
        target = role.lower().strip()
        out: list[ClerkUserLite] = []
        offset = 0
        try:
            async with self._client() as client:
                while offset < _MAX_SCAN:
                    resp = await client.get(
                        "/users",
                        params={"limit": _PAGE, "offset": offset, "order_by": "-created_at"},
                    )
                    self._raise_for_status(resp, "clerk_list_users_failed")
                    page = _as_user_list(resp.json())
                    if not page:
                        break
                    out.extend(u for u in (_to_lite(x) for x in page) if u.role == target)
                    if len(page) < _PAGE:
                        break
                    offset += _PAGE
        except httpx.RequestError as exc:
            log.exception("clerk_list_users_error")
            raise IntegrationError("Clerk request failed", code="clerk_unreachable") from exc
        return out

    @staticmethod
    def _raise_for_status(resp: httpx.Response, code: str) -> None:
        if resp.status_code >= 400:
            log.warning(code, status=resp.status_code, body=resp.text[:300])
            raise IntegrationError("Clerk management API error", code=code)
