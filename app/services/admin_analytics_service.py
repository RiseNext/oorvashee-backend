"""AdminAnalyticsService — dashboard-ready analytics aggregates.

Service orchestrates `AnalyticsRepository` queries and shapes them into
the response models. Empty buckets in the time-series are filled here
so the chart has a dense x-axis even on a quiet day.

AOV math lives here, not in SQL — divide-by-zero handling on
no-paid-orders is a service concern.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.repositories.analytics_repo import AnalyticsRepository
from app.schemas.admin_analytics import (
    AnalyticsOverview,
    CustomerMetrics,
    FulfillmentKPI,
    Granularity,
    TimeRangeEcho,
    TopCategoriesResponse,
    TopCategoryRow,
    TopProductRow,
    TopProductsResponse,
    TrendBucket,
    TrendResponse,
)
from app.services.base import BaseService

log = get_logger(__name__)

# Hard caps — defended at the service boundary in addition to schema.
# Beyond 366 days a single Postgres node starts to feel the heat; the
# right answer at that point is a separate analytics store, not raising
# this limit.
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30
MAX_TOP_N = 100
DEFAULT_TOP_N = 10


class AdminAnalyticsService(BaseService):
    @property
    def repo(self) -> AnalyticsRepository:
        return AnalyticsRepository(self.session)

    # ======================================================================
    # Overview
    # ======================================================================

    async def overview(
        self, *, since: datetime, until: datetime
    ) -> AnalyticsOverview:
        _validate_range(since, until)
        agg = await self.repo.overview_aggregates(since=since, until=until)
        new_customers = await self.repo.new_customers(since=since, until=until)

        paid_orders = int(agg["paid_orders"])
        total_revenue = Decimal(agg["total_revenue"])  # type: ignore[arg-type]
        aov = _safe_aov(total_revenue, paid_orders)

        return AnalyticsOverview(
            range=TimeRangeEcho(since=since, until=until),
            total_revenue=total_revenue,
            total_orders=int(agg["total_orders"]),
            paid_orders=paid_orders,
            average_order_value=aov,
            units_sold=int(agg["units_sold"]),
            new_customers=new_customers,
            guest_orders=int(agg["guest_orders"]),
        )

    # ======================================================================
    # Trends
    # ======================================================================

    async def trend(
        self,
        *,
        since: datetime,
        until: datetime,
        granularity: Granularity,
    ) -> TrendResponse:
        _validate_range(since, until)
        rows = await self.repo.revenue_orders_units_trend(
            since=since, until=until, granularity=granularity.value,
        )

        # Fill missing buckets so the chart x-axis is dense. Postgres
        # only returns buckets where activity exists; we synthesise
        # zeros for quiet days so the trend line drops to zero visibly.
        observed = {row[0]: row for row in rows}
        all_buckets = _bucket_range(since, until, granularity)
        out: list[TrendBucket] = []
        for b in all_buckets:
            if b in observed:
                _, revenue, orders, units = observed[b]
                out.append(
                    TrendBucket(
                        bucket=b,
                        revenue=revenue,
                        orders=orders,
                        units=units,
                    )
                )
            else:
                out.append(
                    TrendBucket(
                        bucket=b,
                        revenue=Decimal("0"),
                        orders=0,
                        units=0,
                    )
                )
        return TrendResponse(
            range=TimeRangeEcho(since=since, until=until, granularity=granularity),
            buckets=out,
        )

    # ======================================================================
    # Top products / categories
    # ======================================================================

    async def top_products(
        self,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        sort_by: str,
    ) -> TopProductsResponse:
        _validate_range(since, until)
        _validate_top_n(limit)
        _validate_sort(sort_by)
        rows = await self.repo.top_products(
            since=since, until=until, limit=limit, sort_by=sort_by,
        )
        return TopProductsResponse(
            range=TimeRangeEcho(since=since, until=until),
            rows=[
                TopProductRow(
                    product_id=r[0],
                    product_name=r[1],
                    product_slug=r[2],
                    units_sold=r[3],
                    revenue=r[4],
                    orders=r[5],
                )
                for r in rows
            ],
        )

    async def top_categories(
        self,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        sort_by: str,
    ) -> TopCategoriesResponse:
        _validate_range(since, until)
        _validate_top_n(limit)
        _validate_sort(sort_by)
        rows = await self.repo.top_categories(
            since=since, until=until, limit=limit, sort_by=sort_by,
        )
        return TopCategoriesResponse(
            range=TimeRangeEcho(since=since, until=until),
            rows=[
                TopCategoryRow(
                    category_id=r[0],
                    category_slug=r[1],
                    category_name=r[2],
                    kind=r[3],
                    units_sold=r[4],
                    revenue=r[5],
                    products_sold=r[6],
                )
                for r in rows
            ],
        )

    # ======================================================================
    # Fulfillment KPIs
    # ======================================================================

    async def fulfillment_kpis(self) -> FulfillmentKPI:
        today_start = _utc_today_start()
        agg = await self.repo.fulfillment_kpis(today_start=today_start)
        return FulfillmentKPI(**agg)

    # ======================================================================
    # Customer metrics
    # ======================================================================

    async def customers(
        self, *, since: datetime, until: datetime
    ) -> CustomerMetrics:
        _validate_range(since, until)
        agg = await self.repo.customer_metrics(since=since, until=until)
        return CustomerMetrics(
            range=TimeRangeEcho(since=since, until=until),
            new_customers=agg["new_customers"],
            repeat_customers=agg["repeat_customers"],
            returning_customers=agg["returning_customers"],
            guest_orders=agg["guest_orders"],
            total_active_customers=agg["total_active_customers"],
        )


# =============================================================================
# Module helpers (pure functions, unit-testable)
# =============================================================================


def _validate_range(since: datetime, until: datetime) -> None:
    if since >= until:
        raise ValidationError(
            "`since` must be strictly before `until`",
            code="invalid_time_range",
            extra={"since": since.isoformat(), "until": until.isoformat()},
        )
    span = until - since
    if span > timedelta(days=MAX_RANGE_DAYS):
        raise ValidationError(
            f"Range exceeds {MAX_RANGE_DAYS}-day cap",
            code="time_range_too_large",
            extra={"max_days": MAX_RANGE_DAYS, "requested_days": span.days},
        )


def _validate_top_n(limit: int) -> None:
    if not (1 <= limit <= MAX_TOP_N):
        raise ValidationError(
            f"limit must be in [1, {MAX_TOP_N}]",
            code="invalid_top_n",
            extra={"limit": limit, "max": MAX_TOP_N},
        )


def _validate_sort(sort_by: str) -> None:
    if sort_by not in ("revenue", "units"):
        raise ValidationError(
            f"sort_by must be 'revenue' or 'units' (got '{sort_by}')",
            code="invalid_sort_by",
        )


def _safe_aov(revenue: Decimal, paid_orders: int) -> Decimal:
    """`revenue / paid_orders` rounded to 2dp. Returns 0 on empty input.

    `ROUND_HALF_UP` matches finance reporting conventions (banker's
    rounding would surprise admins).
    """
    if paid_orders <= 0:
        return Decimal("0.00")
    return (revenue / Decimal(paid_orders)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _bucket_range(
    since: datetime, until: datetime, granularity: Granularity
) -> list[date]:
    """Enumerate every bucket-start date in the half-open range `[since, until)`.

    Postgres-aligned: `date_trunc('day'|'week'|'month', …)` returns the
    bucket START. A bucket is included iff its start datetime is strictly
    less than `until`. A range that lands exactly on a bucket boundary
    (e.g. `until = 2026-05-08 00:00`) excludes that final bucket — same
    semantics as the aggregate query's `created_at < until` filter.

    Week buckets start on Monday (Postgres default for ISO week).
    Safety brake at 5k buckets so a pathological caller can't hang us.
    """
    current = _trunc_date(since, granularity)
    buckets: list[date] = []
    while True:
        bucket_start_dt = datetime.combine(
            current, datetime.min.time(), tzinfo=UTC
        )
        if bucket_start_dt >= until:
            break
        buckets.append(current)
        current = _bucket_advance(current, granularity)
        if len(buckets) > 5_000:
            break
    return buckets


def _trunc_date(dt: datetime, granularity: Granularity) -> date:
    d = dt.date()
    if granularity is Granularity.DAY:
        return d
    if granularity is Granularity.WEEK:
        # Postgres `date_trunc('week', ...)` returns Monday.
        return d - timedelta(days=d.weekday())
    # MONTH
    return d.replace(day=1)


def _bucket_advance(d: date, granularity: Granularity) -> date:
    if granularity is Granularity.DAY:
        return d + timedelta(days=1)
    if granularity is Granularity.WEEK:
        return d + timedelta(days=7)
    # MONTH — calendar-aware
    if d.month == 12:
        return d.replace(year=d.year + 1, month=1)
    return d.replace(month=d.month + 1)


def default_range() -> tuple[datetime, datetime]:
    """30-day window ending now (UTC). Used by router when client omits since/until."""
    until = datetime.now(UTC)
    return until - timedelta(days=DEFAULT_RANGE_DAYS), until


def _utc_today_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
