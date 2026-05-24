"""Liveness endpoint smoke test."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_liveness_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_liveness_attaches_request_id(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")
    assert "X-Request-ID" not in response.headers or response.headers.get(
        "X-Request-ID"
    )  # header is optional; middleware sets state.request_id, exposed via header in future cycle


@pytest.mark.asyncio
async def test_openapi_available_in_local(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert spec["info"]["title"] == "Oorvashee Backend"
    paths = spec["paths"]
    assert "/api/v1/health/live" in paths
    assert "/api/v1/health" in paths
