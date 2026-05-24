"""require_role dependency factory — pure logic, no FastAPI runtime."""

from __future__ import annotations

import pytest

from app.core.exceptions import PermissionDeniedError
from app.core.security import Principal, require_admin, require_role


def _principal(role: str) -> Principal:
    return Principal(
        clerk_user_id="user_test",
        email="x@example.com",
        role=role,
        raw_claims={},
    )


def test_require_role_rejects_empty_set() -> None:
    with pytest.raises(ValueError):
        require_role()


async def test_require_role_normalises_input() -> None:
    """Factory accepts mixed-case input but compares lowercase."""
    dep = require_role("Admin", "  STAFF ")
    await dep(_principal("staff"))  # type: ignore[arg-type]
    await dep(_principal("admin"))  # type: ignore[arg-type]


async def test_require_role_passes_matching() -> None:
    dep = require_role("admin", "staff")
    result = await dep(_principal("admin"))  # type: ignore[arg-type]
    assert result.role == "admin"


async def test_require_role_rejects_mismatch() -> None:
    dep = require_role("admin")
    with pytest.raises(PermissionDeniedError):
        await dep(_principal("customer"))  # type: ignore[arg-type]


async def test_require_role_is_case_insensitive_on_principal() -> None:
    """JWT claims arrive verbatim from Clerk; we must lowercase before compare."""
    dep = require_role("admin")
    await dep(_principal("ADMIN"))  # type: ignore[arg-type]


async def test_require_admin_still_works() -> None:
    """Backwards compat: existing call sites depend on `require_admin` directly."""
    await require_admin(_principal("admin"))  # type: ignore[arg-type]
    with pytest.raises(PermissionDeniedError):
        await require_admin(_principal("customer"))  # type: ignore[arg-type]
