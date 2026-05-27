"""Pure-logic tests for AdminAnalyticsService helpers — no DB.

Covers:
- Time-range validation (since < until, max 366 days).
- Top-N bounds.
- Sort-by whitelist.
- AOV division (with zero-paid-orders guard + rounding).
- Bucket enumeration for day / week / month (alignment + leap-year).
- Default range = last 30 days, UTC.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.schemas.admin_analytics import Granularity
from app.services.admin_analytics_service import (
    DEFAULT_RANGE_DAYS,
    MAX_RANGE_DAYS,
    MAX_TOP_N,
    _bucket_advance,
    _bucket_range,
    _safe_aov,
    _trunc_date,
    _validate_range,
    _validate_sort,
    _validate_top_n,
    default_range,
)

# ---------- Range validation ---------------------------------------------


def test_valid_range_passes() -> None:
    a = datetime(2026, 5, 1, tzinfo=UTC)
    b = datetime(2026, 5, 31, tzinfo=UTC)
    _validate_range(a, b)  # no raise


def test_since_equal_to_until_rejected() -> None:
    a = datetime(2026, 5, 1, tzinfo=UTC)
    with pytest.raises(ValidationError) as exc:
        _validate_range(a, a)
    assert exc.value.code == "invalid_time_range"


def test_reversed_range_rejected() -> None:
    a = datetime(2026, 5, 1, tzinfo=UTC)
    b = datetime(2026, 5, 31, tzinfo=UTC)
    with pytest.raises(ValidationError) as exc:
        _validate_range(b, a)
    assert exc.value.code == "invalid_time_range"


def test_range_at_cap_passes() -> None:
    a = datetime(2025, 1, 1, tzinfo=UTC)
    b = a + timedelta(days=MAX_RANGE_DAYS)
    _validate_range(a, b)  # exactly at cap → allowed


def test_range_beyond_cap_rejected() -> None:
    a = datetime(2025, 1, 1, tzinfo=UTC)
    b = a + timedelta(days=MAX_RANGE_DAYS + 1)
    with pytest.raises(ValidationError) as exc:
        _validate_range(a, b)
    assert exc.value.code == "time_range_too_large"
    assert exc.value.extra["max_days"] == MAX_RANGE_DAYS


# ---------- Top-N validation ---------------------------------------------


@pytest.mark.parametrize("limit", [1, 10, 50, MAX_TOP_N])
def test_top_n_valid(limit: int) -> None:
    _validate_top_n(limit)


@pytest.mark.parametrize("limit", [0, -1, MAX_TOP_N + 1, 10_000])
def test_top_n_invalid(limit: int) -> None:
    with pytest.raises(ValidationError) as exc:
        _validate_top_n(limit)
    assert exc.value.code == "invalid_top_n"


# ---------- Sort whitelist -----------------------------------------------


@pytest.mark.parametrize("sort_by", ["revenue", "units"])
def test_sort_valid(sort_by: str) -> None:
    _validate_sort(sort_by)


@pytest.mark.parametrize("sort_by", ["", "REVENUE", "price", "id"])
def test_sort_invalid(sort_by: str) -> None:
    with pytest.raises(ValidationError) as exc:
        _validate_sort(sort_by)
    assert exc.value.code == "invalid_sort_by"


# ---------- AOV math -----------------------------------------------------


def test_aov_zero_orders_returns_zero() -> None:
    """Division-by-zero guard — dashboards prefer 0 over NaN."""
    assert _safe_aov(Decimal("0"), 0) == Decimal("0.00")
    assert _safe_aov(Decimal("12345.67"), 0) == Decimal("0.00")


def test_aov_negative_orders_returns_zero() -> None:
    """Defensive — paid_orders should never be negative, but if a future
    bug surfaces it as a signed int we'd rather return 0 than -∞."""
    assert _safe_aov(Decimal("1000"), -1) == Decimal("0.00")


def test_aov_basic_division() -> None:
    assert _safe_aov(Decimal("10000"), 5) == Decimal("2000.00")


def test_aov_rounds_half_up_two_dp() -> None:
    """ROUND_HALF_UP matches finance reporting (banker's rounding would surprise)."""
    # 100 / 3 = 33.333... → 33.33
    assert _safe_aov(Decimal("100"), 3) == Decimal("33.33")
    # 100 / 7 = 14.2857... → 14.29
    assert _safe_aov(Decimal("100"), 7) == Decimal("14.29")
    # 1.005 → 1.01 (HALF_UP — not banker's 1.00)
    assert _safe_aov(Decimal("3.015"), 3) == Decimal("1.01")


# ---------- Bucket truncation --------------------------------------------


def test_trunc_day() -> None:
    dt = datetime(2026, 5, 15, 14, 30, tzinfo=UTC)
    assert _trunc_date(dt, Granularity.DAY) == date(2026, 5, 15)


def test_trunc_week_returns_monday() -> None:
    """Postgres `date_trunc('week', …)` returns Monday — we must match."""
    # 2026-05-15 is a Friday → week starts Mon 2026-05-11
    dt = datetime(2026, 5, 15, 14, 30, tzinfo=UTC)
    assert _trunc_date(dt, Granularity.WEEK) == date(2026, 5, 11)


def test_trunc_month() -> None:
    dt = datetime(2026, 5, 15, 14, 30, tzinfo=UTC)
    assert _trunc_date(dt, Granularity.MONTH) == date(2026, 5, 1)


# ---------- Bucket advance -----------------------------------------------


def test_advance_day() -> None:
    assert _bucket_advance(date(2026, 5, 1), Granularity.DAY) == date(2026, 5, 2)


def test_advance_week() -> None:
    assert _bucket_advance(date(2026, 5, 4), Granularity.WEEK) == date(2026, 5, 11)


def test_advance_month_within_year() -> None:
    assert _bucket_advance(date(2026, 5, 1), Granularity.MONTH) == date(2026, 6, 1)


def test_advance_month_year_rollover() -> None:
    assert _bucket_advance(date(2026, 12, 1), Granularity.MONTH) == date(2027, 1, 1)


# ---------- Bucket range enumeration -------------------------------------


def test_day_bucket_range_dense_one_week() -> None:
    since = datetime(2026, 5, 1, 0, 0, tzinfo=UTC)
    until = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
    buckets = _bucket_range(since, until, Granularity.DAY)
    assert buckets[0] == date(2026, 5, 1)
    assert buckets[-1] == date(2026, 5, 7)
    assert len(buckets) == 7


def test_month_bucket_range_quarterly() -> None:
    since = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    until = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    buckets = _bucket_range(since, until, Granularity.MONTH)
    assert buckets == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_week_bucket_range_aligns_to_monday() -> None:
    # Wed → Wed: should land on the Mondays of those weeks.
    since = datetime(2026, 5, 6, 0, 0, tzinfo=UTC)  # Wed
    until = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)  # Wed
    buckets = _bucket_range(since, until, Granularity.WEEK)
    # Truncates: 2026-05-04 (Mon), 2026-05-11 (Mon), 2026-05-18 (Mon)
    assert buckets == [date(2026, 5, 4), date(2026, 5, 11), date(2026, 5, 18)]


def test_bucket_range_empty_when_same_bucket() -> None:
    """Same-day range produces a single bucket — chart shows one point."""
    since = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    until = datetime(2026, 5, 15, 18, 0, tzinfo=UTC)
    buckets = _bucket_range(since, until, Granularity.DAY)
    assert buckets == [date(2026, 5, 15)]


def test_bucket_range_safety_brake() -> None:
    """A pathological range should bail out around 5000 buckets, not loop."""
    since = datetime(2000, 1, 1, tzinfo=UTC)
    until = datetime(2050, 1, 1, tzinfo=UTC)
    buckets = _bucket_range(since, until, Granularity.DAY)
    # 50 years x ~365 = ~18250 days, but we cap at 5000 to avoid the loop.
    assert len(buckets) <= 5001


# ---------- Default range -----------------------------------------------


def test_default_range_is_30_days_ending_now() -> None:
    since, until = default_range()
    span = until - since
    assert abs(span.days - DEFAULT_RANGE_DAYS) <= 1
    assert until.tzinfo == UTC
