"""Health & readiness endpoints.

- `/health/live` — process is up. Returns 200 without touching the DB.
  Used by orchestrators that should NOT route around a brief DB blip.

- `/health` — full readiness. Verifies DB connectivity. Returns 503 if
  the DB is unreachable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.config import Settings, get_settings
from app.db.healthcheck import db_ping
from app.schemas.common import HealthResponse, LiveResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health/live",
    response_model=LiveResponse,
    summary="Liveness probe (no DB hit)",
)
async def liveness() -> LiveResponse:
    return LiveResponse()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Readiness probe (verifies DB)",
)
async def readiness(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    db_ok = await db_ping()
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        db="connected" if db_ok else "disconnected",
        version=settings.app_version,
        commit=None,  # populated by CI in a future revision
        env=settings.app_env.value,
    )
