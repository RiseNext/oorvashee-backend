"""FastAPI application factory + entrypoint.

Run locally:
    uv run uvicorn app.main:app --reload

Production (Gunicorn driving Uvicorn workers — see Dockerfile CMD):
    gunicorn app.main:app -k uvicorn.workers.UvicornWorker
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app import __version__
from app.api.v1 import api_v1_router
from app.core.body_size import BodySizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.core.rate_limit import install_rate_limiter
from app.core.security_headers import SecurityHeadersMiddleware
from app.core.sentry import init_sentry
from app.db.session import (
    build_engine,
    build_sessionmaker,
    dispose_engine,
    set_db_state,
)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Build long-lived resources at startup, dispose on shutdown."""
    settings = get_settings()
    configure_logging(settings)
    init_sentry(settings)
    log = get_logger("app.lifespan")

    engine = build_engine(settings)
    sessionmaker = build_sessionmaker(engine)
    set_db_state(engine, sessionmaker)

    # `sentry_enabled` reflects what the Sentry init actually decided —
    # a non-empty but malformed DSN counts as disabled (see core/sentry.py).
    dsn = (settings.sentry_dsn or "").strip()
    sentry_active = dsn.startswith(("http://", "https://"))
    log.info(
        "app_startup",
        env=settings.app_env.value,
        version=settings.app_version,
        rate_limit_enabled=settings.rate_limit_enabled,
        sentry_enabled=sentry_active,
    )
    try:
        yield
    finally:
        log.info("app_shutdown")
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory. Useful for tests that want a customised app."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Oorvashee Backend",
        version=__version__,
        description=(
            "Backend API for Oorvashee Saree House — premium ethnic e-commerce. "
            "Source of truth: ai-context/PRD.md."
        ),
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_prod else None,
        redoc_url="/redoc" if not settings.is_prod else None,
        openapi_url="/openapi.json" if not settings.is_prod else None,
    )

    # ---- Middleware ----
    # Starlette runs middleware from OUTERMOST (added LAST) to INNERMOST
    # (added FIRST). Read this block top-to-bottom to follow request flow:
    #
    #   [client] → CORS → SecurityHeaders → BodySizeLimit → SlowAPI →
    #   GZip → RequestContext → routes → reverse stack → [client]
    #
    # CORS is OUTERMOST so preflights never touch other middleware.
    # SecurityHeaders is high so 4xx/5xx responses still get hardened.
    # BodySizeLimit is BEFORE SlowAPI so a 50 MB junk POST is dropped
    #   before counting against rate-limit buckets.
    # GZip wraps responses, not requests — order vs CORS doesn't matter.
    # RequestContext is INNERMOST so middleware errors above it surface
    #   without confusing log lines from the access-log finally-block.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            REQUEST_ID_HEADER,
        ],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
        exempt_path_prefixes=settings.body_size_exempt_path_prefixes,
    )
    # SlowAPI must be added BEFORE RequestContextMiddleware (i.e. outer
    # in execution order) so that the rate-limit decision happens before
    # we allocate per-request logging context.
    install_rate_limiter(app, settings)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(RequestContextMiddleware)

    # ---- Exception handlers ----
    register_exception_handlers(app)

    # ---- Routers ----
    app.include_router(api_v1_router, prefix="/api/v1")

    return app


app = create_app()
