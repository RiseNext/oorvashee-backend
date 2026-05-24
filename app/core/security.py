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
