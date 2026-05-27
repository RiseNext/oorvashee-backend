"""Analytics schemas — read-only dashboard shapes.

Design notes:
- All time-range filtered. Default window = last 30 days; hard cap 366
  days (1 year + leap day) to bound query cost on a single Postgres
  instance. Tune up when a Redis read-cache or a separate analytics
  store lands.
- Granularity is `day | week | month`. Buckets use `date_trunc()` on
  `orders.created_at`. `created_at` is TIMESTAMPTZ so Postgres handles
  timezone correctly without our help.
- Revenue is the sum of `orders.total` for orders with
  `payment_status='paid'`. COD-pending and refunded orders are NOT
  included — only money actually collected counts.
- Currency = INR throughout (PRD locks it). Schema does not surface a
  currency field per response; callers know it's INR.
- Empty-state: every endpoint returns zeros / empty lists for an empty
  window, NEVER 404. Dashboards prefer "0 sales" over a missing tile.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


# ---------- Time range echo ----------------------------------------------


class TimeRangeEcho(BaseModel):
    """Echoed on every analytics response so the dashboard can label the
    chart axes ("Revenue · 1 Oct → 31 Oct") without re-deriving."""

    model_config = ConfigDict(from_attributes=True)

    since: datetime
    until: datetime
    granularity: Granularity | None = None


# ---------- Overview KPIs -------------------------------------------------


class AnalyticsOverview(BaseModel):
    """Top-line dashboard tiles."""

    model_config = ConfigDict(from_attributes=True)

    range: TimeRangeEcho
    total_revenue: Decimal = Field(description="Sum of `orders.total` where payment_status=paid.")
    total_orders: int = Field(description="Count of orders created in range (any payment_status).")
    paid_orders: int = Field(description="Subset of total_orders that were paid.")
    average_order_value: Decimal = Field(
        description="`total_revenue / paid_orders` rounded to 2dp; 0 when no paid orders."
    )
    units_sold: int = Field(description="`SUM(quantity)` across line items of paid orders.")
    new_customers: int = Field(
        description="Distinct `user_id` whose `users.created_at` falls in range."
    )
    guest_orders: int = Field(description="Orders in range with `user_id IS NULL`.")


# ---------- Trend (time series) ------------------------------------------


class TrendBucket(BaseModel):
    """One bucket of a time-series trend. `bucket` is the start-of-period."""

    model_config = ConfigDict(from_attributes=True)

    bucket: date
    revenue: Decimal
    orders: int
    units: int


class TrendResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    range: TimeRangeEcho
    buckets: list[TrendBucket]


# ---------- Top products / categories -------------------------------------


class TopProductRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    product_name: str
    product_slug: str
    units_sold: int
    revenue: Decimal
    orders: int = Field(
        description="Distinct order count — useful to spot single-bulk-order outliers."
    )


class TopProductsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    range: TimeRangeEcho
    rows: list[TopProductRow]


class TopCategoryRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    category_id: uuid.UUID
    category_slug: str
    category_name: str
    kind: str
    units_sold: int
    revenue: Decimal
    products_sold: int = Field(
        description="Distinct product count contributing — wide vs deep performers."
    )


class TopCategoriesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    range: TimeRangeEcho
    rows: list[TopCategoryRow]


# ---------- Fulfillment KPI ----------------------------------------------


class FulfillmentKPI(BaseModel):
    """Operational fulfillment tiles. NOT time-range filtered — these are
    'right now' counters. Time-range filtering doesn't make sense for
    'orders awaiting packing today'."""

    model_config = ConfigDict(from_attributes=True)

    awaiting_packing: int = Field(
        description="status=placed AND payment_status in (paid, cod_pending)."
    )
    awaiting_shipment: int = Field(description="status=packed.")
    in_transit: int = Field(description="status=shipped (handed to courier, not yet delivered).")
    delivered_today: int = Field(description="status=delivered AND delivered_at >= today (UTC).")
    shipped_missing_tracking: int = Field(
        description="status=shipped AND shipment.tracking_id IS NULL — data hygiene flag."
    )
    cod_outstanding: int = Field(
        description=(
            "payment_method=cod AND payment_status=cod_pending AND "
            "status NOT IN (delivered, cancelled)."
        )
    )


# ---------- Customer metrics ---------------------------------------------


class CustomerMetrics(BaseModel):
    """All ranges relative to the given window."""

    model_config = ConfigDict(from_attributes=True)

    range: TimeRangeEcho
    new_customers: int = Field(description="`users.created_at` in range.")
    repeat_customers: int = Field(
        description="Customers with ≥2 paid orders in range."
    )
    returning_customers: int = Field(
        description="Customers who ordered in range AND had a paid order before `range.since`."
    )
    guest_orders: int = Field(description="Orders in range with no user_id.")
    total_active_customers: int = Field(
        description="Distinct user_id with at least one order in range."
    )
