"""Admin analytics endpoints — dashboard KPIs + trends + top-N.

Read-only. Every response includes `Cache-Control: max-age=60, private`
so a fronting CDN or browser can shield repeated dashboard polls without
hammering Postgres.

Time range defaults to last 30 days when client omits `since`/`until`.
Hard cap: 366 days at the service layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.core.security import require_role
from app.db.deps import DbSession
from app.schemas.admin_analytics import (
    AnalyticsOverview,
    CustomerMetrics,
    FulfillmentKPI,
    Granularity,
    TopCategoriesResponse,
    TopProductsResponse,
    TrendResponse,
)
from app.services.admin_analytics_service import (
    DEFAULT_TOP_N,
    AdminAnalyticsService,
    default_range,
)

router = APIRouter(dependencies=[Depends(require_role("admin"))])


CACHE_HEADER = "max-age=60, private"


def _attach_cache(response: Response) -> None:
    """Same Cache-Control on every analytics response. Centralising the
    header value avoids drift across endpoints."""
    response.headers["Cache-Control"] = CACHE_HEADER


def _resolve_range(
    since: datetime | None, until: datetime | None
) -> tuple[datetime, datetime]:
    default_since, default_until = default_range()
    return (since or default_since, until or default_until)


# =============================================================================
# Overview
# =============================================================================


@router.get(
    "/overview",
    response_model=AnalyticsOverview,
    summary="Top-line dashboard tiles: revenue, orders, AOV, units, new customers",
)
async def overview(
    response: Response,
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> AnalyticsOverview:
    s, u = _resolve_range(since, until)
    _attach_cache(response)
    return await AdminAnalyticsService(session).overview(since=s, until=u)


# =============================================================================
# Trend (time series)
# =============================================================================


@router.get(
    "/trend",
    response_model=TrendResponse,
    summary="Revenue + orders + units time series with dense buckets",
)
async def trend(
    response: Response,
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    granularity: Annotated[Granularity, Query()] = Granularity.DAY,
) -> TrendResponse:
    s, u = _resolve_range(since, until)
    _attach_cache(response)
    return await AdminAnalyticsService(session).trend(
        since=s, until=u, granularity=granularity,
    )


# =============================================================================
# Top-N
# =============================================================================


@router.get(
    "/top-products",
    response_model=TopProductsResponse,
    summary="Top products by revenue or units in range",
)
async def top_products(
    response: Response,
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_TOP_N,
    sort_by: Annotated[str, Query(pattern=r"^(revenue|units)$")] = "revenue",
) -> TopProductsResponse:
    s, u = _resolve_range(since, until)
    _attach_cache(response)
    return await AdminAnalyticsService(session).top_products(
        since=s, until=u, limit=limit, sort_by=sort_by,
    )


@router.get(
    "/top-categories",
    response_model=TopCategoriesResponse,
    summary="Top categories by revenue or units in range",
)
async def top_categories(
    response: Response,
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = DEFAULT_TOP_N,
    sort_by: Annotated[str, Query(pattern=r"^(revenue|units)$")] = "revenue",
) -> TopCategoriesResponse:
    s, u = _resolve_range(since, until)
    _attach_cache(response)
    return await AdminAnalyticsService(session).top_categories(
        since=s, until=u, limit=limit, sort_by=sort_by,
    )


# =============================================================================
# Fulfillment KPI (no time range — "right now" counters)
# =============================================================================


@router.get(
    "/fulfillment",
    response_model=FulfillmentKPI,
    summary="Operational fulfillment counters: awaiting packing/shipment/transit etc.",
)
async def fulfillment_kpis(
    response: Response, session: DbSession
) -> FulfillmentKPI:
    _attach_cache(response)
    return await AdminAnalyticsService(session).fulfillment_kpis()


# =============================================================================
# Customer metrics
# =============================================================================


@router.get(
    "/customers",
    response_model=CustomerMetrics,
    summary="New / returning / repeat / guest customer counts for the window",
)
async def customer_metrics(
    response: Response,
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
) -> CustomerMetrics:
    s, u = _resolve_range(since, until)
    _attach_cache(response)
    return await AdminAnalyticsService(session).customers(since=s, until=u)
