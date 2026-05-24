"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Force a deterministic test environment BEFORE importing app.
os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:3000")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://oorvashee:oorvashee@localhost:5432/oorvashee_test",
)
os.environ.setdefault("CLERK_ISSUER", "https://example.clerk.accounts.dev")
os.environ.setdefault(
    "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
)
os.environ.setdefault("CLERK_AUTHORIZED_PARTIES", "http://localhost:3000")


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """ASGI in-process client. No real network, no real DB calls expected
    in unit tests of the health-live endpoint.
    """
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
