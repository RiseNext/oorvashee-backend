"""Clerk JWT verification — foundation only.

Cycle 0 ships the verifier and dependency stubs. Cycle 4 wires them to the
local `user` table (created via Clerk webhook) and to admin role checks.

Design (see AUTH_FLOW.md):
    1. Fetch Clerk JWKS, cache in-memory with TTL.
    2. PyJWT verifies signature + standard claims (exp, nbf, iss).
    3. Validate `azp` against configured authorized parties.
    4. Return decoded claims; service layer maps `sub` → local user.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.logging import get_logger

log = get_logger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


# =============================================================================
# JWKS cache
# =============================================================================


@dataclass
class _JwksCacheEntry:
    client: PyJWKClient
    fetched_at: float


class ClerkJwksCache:
    """Async-safe in-memory JWKS cache. One entry per JWKS URL."""

    def __init__(self, *, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, _JwksCacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, jwks_url: str) -> PyJWKClient:
        now = time.monotonic()
        entry = self._entries.get(jwks_url)
        if entry and (now - entry.fetched_at) < self._ttl:
            return entry.client

        async with self._lock:
            entry = self._entries.get(jwks_url)
            if entry and (time.monotonic() - entry.fetched_at) < self._ttl:
                return entry.client

            client = await asyncio.to_thread(PyJWKClient, jwks_url, cache_keys=True)
            self._entries[jwks_url] = _JwksCacheEntry(
                client=client, fetched_at=time.monotonic()
            )
            log.info("clerk_jwks_refreshed", url=jwks_url)
            return client

    async def force_refresh(self, jwks_url: str) -> PyJWKClient:
        self._entries.pop(jwks_url, None)
        return await self.get(jwks_url)


# Module-level singleton; rebuilt on app startup with the right TTL.
_jwks_cache: ClerkJwksCache | None = None


def _cache(settings: Settings) -> ClerkJwksCache:
    global _jwks_cache
    if _jwks_cache is None:
        _jwks_cache = ClerkJwksCache(ttl_seconds=settings.clerk_jwks_cache_ttl_seconds)
    return _jwks_cache


async def verify_clerk_jwt(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a Clerk-issued JWT. Returns decoded claims or raises AuthenticationError."""
    cache = _cache(settings)
    jwks_url = str(settings.clerk_jwks_url)

    try:
        client = await cache.get(jwks_url)
        signing_key = await asyncio.to_thread(
            client.get_signing_key_from_jwt, token
        )
    except (httpx.HTTPError, InvalidTokenError) as exc:
        log.warning("clerk_jwks_lookup_failed", error=str(exc))
        raise AuthenticationError("Unable to verify token signing key") from exc

    decode_kwargs: dict[str, Any] = {
        "algorithms": ["RS256"],
        "issuer": str(settings.clerk_issuer).rstrip("/"),
        "options": {
            "require": ["exp", "iat", "iss", "sub"],
            "verify_aud": bool(settings.clerk_audience),
        },
    }
    if settings.clerk_audience:
        decode_kwargs["audience"] = settings.clerk_audience

    try:
        claims: dict[str, Any] = jwt.decode(
            token, signing_key.key, **decode_kwargs
        )
    except InvalidTokenError as exc:
        log.warning("clerk_jwt_invalid", error=str(exc))
        raise AuthenticationError("Invalid or expired token") from exc

    # Authorized-party check defends against tokens issued for a different origin.
    if settings.clerk_authorized_parties:
        azp = claims.get("azp")
        if azp and azp not in settings.clerk_authorized_parties:
            log.warning("clerk_jwt_azp_mismatch", azp=azp)
            raise AuthenticationError("Token authorized party not allowed")

    return claims


# =============================================================================
# Principal — minimal placeholder until app/models/user lands in Cycle 1/4
# =============================================================================


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated caller, derived from a verified Clerk JWT.

    In Cycle 4 this gets replaced with (or supplemented by) the local `User`
    model row. For now it carries just what we need to make admin checks work.
    """

    clerk_user_id: str
    email: str | None
    role: str
    raw_claims: dict[str, Any]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _principal_from_claims(claims: dict[str, Any]) -> Principal:
    # Clerk surfaces public_metadata claims via the JWT template the frontend
    # uses (e.g. `{"role": "{{user.public_metadata.role}}"}`). Default to
    # 'customer' if absent.
    role = str(claims.get("role") or claims.get("public_metadata", {}).get("role") or "customer")
    email = claims.get("email")
    return Principal(
        clerk_user_id=str(claims["sub"]),
        email=email,
        role=role,
        raw_claims=claims,
    )


# =============================================================================
# FastAPI dependencies
# =============================================================================


SettingsDep = Annotated[Settings, Depends(get_settings)]
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_current_principal_optional(
    _request: Request, settings: SettingsDep, credentials: BearerDep
) -> Principal | None:
    """Returns the caller if a valid Bearer token is present, else None.

    Use on routes that work for both guest and authenticated users (e.g. checkout).
    """
    if credentials is None:
        return None
    if credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authorization scheme must be Bearer")
    claims = await verify_clerk_jwt(credentials.credentials, settings)
    return _principal_from_claims(claims)


async def get_current_principal(
    principal: Annotated[Principal | None, Depends(get_current_principal_optional)],
) -> Principal:
    """Require an authenticated caller. Raises 401 otherwise."""
    if principal is None:
        raise AuthenticationError("Authentication required")
    return principal


async def require_admin(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    """Require admin role. Raises 403 if the caller is authenticated but not admin."""
    if not principal.is_admin:
        log.warning("admin_access_denied", clerk_user_id=principal.clerk_user_id)
        raise PermissionDeniedError("Admin role required")
    return principal


def require_role(*allowed_roles: str):  # type: ignore[no-untyped-def]
    """Dependency factory: caller's role must match one of `allowed_roles`.

    Usage:
        @router.post(
            "/admin/products",
            dependencies=[Depends(require_role("admin"))],
        )
        async def create_product(...): ...

        @router.get(
            "/admin/orders",
            dependencies=[Depends(require_role("admin", "staff"))],
        )
        async def list_orders(...): ...

    Role names match what Clerk emits in `public_metadata.role` (lowercase).
    The check reads the JWT claim — fast, no DB hit. The local `user_roles`
    table stays in sync via the Clerk webhook for admin-list queries +
    audit traceability; this dependency does not consult it.

    `require_admin` is preserved as a standalone function for the existing
    Cycle-0 call sites; use this factory for everything new.
    """
    if not allowed_roles:
        raise ValueError("require_role needs at least one role name")

    normalized = {r.lower().strip() for r in allowed_roles}

    async def _dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        if principal.role.lower() not in normalized:
            log.warning(
                "role_access_denied",
                required=sorted(normalized),
                got=principal.role,
                clerk_user_id=principal.clerk_user_id,
            )
            raise PermissionDeniedError(
                f"Required role(s): {', '.join(sorted(normalized))}",
            )
        return principal

    return _dependency


# =============================================================================
# Local user resolution — auto-provisioning fallback
# =============================================================================
#
# The Clerk webhook (`POST /webhooks/clerk`) is now the PRIMARY user
# provisioning path — it mirrors Clerk users into the local `users` table
# proactively whenever Clerk emits user.created / user.updated / user.deleted.
#
# This dependency stays as a DEFENSIVE FALLBACK against webhook lag — if a
# JWT arrives faster than Svix delivers the user.created event, we still
# materialise the row so the request can complete. The webhook then
# reconciles when it lands (idempotent — see UserSyncService).
#
# Endpoints that need a real `User` row (cart, wishlist, account/*, orders)
# depend on this rather than on `get_current_principal` directly.

from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.core.validators import is_plausible_email  # noqa: E402
from app.db.deps import DbSession  # noqa: E402
from app.models.user import User  # noqa: E402


async def get_current_user(
    principal: Annotated[Principal, Depends(get_current_principal)],
    session: DbSession,
) -> User:
    """Return the local `User` row for the authenticated principal.

    Creates the row on the fly if missing — covers two scenarios:
    1. Clerk webhook hasn't landed yet (Cycle 4).
    2. Webhook lag — JWT issued faster than Svix delivery.

    `email` comes from the JWT `email` claim, which the Clerk **"backend"**
    template (frontend `NEXT_PUBLIC_CLERK_JWT_TEMPLATE`) sets via the
    `{{user.primary_email_address}}` shortcode. We hard-validate it before
    persisting: a missing claim — or an UNRENDERED template literal such as
    `{{user.primary_email_address.email_address}}` from a misconfigured
    template — must never be written, or it poisons UNIQUE(users.email) and
    breaks every subsequent signup.
    """
    stmt = select(User).where(User.clerk_user_id == principal.clerk_user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is not None:
        return user

    # First-touch provisioning. Reject empty AND junk (template-literal) emails
    # loudly so the auth flow gets fixed, rather than storing a poison value.
    if not is_plausible_email(principal.email):
        log.error(
            "user_autoprovision_invalid_email",
            clerk_user_id=principal.clerk_user_id,
        )
        raise AuthenticationError(
            "JWT `email` claim missing or invalid — check the Clerk 'backend' JWT template",
            code="invalid_email_claim",
        )

    # Provision inside a SAVEPOINT so a UNIQUE collision doesn't poison the
    # request transaction. Two collisions are possible:
    #   - a concurrent request already provisioned this clerk user (race) —
    #     re-query by clerk_user_id and return that row; or
    #   - the email belongs to a DIFFERENT clerk user (legacy corrupted data) —
    #     surface a clear 401 instead of a 500.
    try:
        async with session.begin_nested():
            user = User(
                clerk_user_id=principal.clerk_user_id,
                email=principal.email,
            )
            session.add(user)
            await session.flush()
        log.info(
            "user_autoprovisioned",
            user_id=str(user.id),
            clerk_user_id=principal.clerk_user_id,
        )
        return user
    except IntegrityError:
        existing = (
            await session.execute(
                select(User).where(User.clerk_user_id == principal.clerk_user_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        log.error(
            "user_autoprovision_email_conflict",
            clerk_user_id=principal.clerk_user_id,
        )
        raise AuthenticationError(
            "Could not provision account — email already in use by another identity.",
            code="email_conflict",
        ) from None
