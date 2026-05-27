"""Pure-logic tests for AdminCustomerService — no DB.

Covers:
- KPI math: AOV (divide-by-zero, rounding), days_since_last_order with
  tz-aware vs naive datetimes, account_age_days with clock-skew defense.
- Audit-log → activity-event mapping (admin_note inclusion, other ignored).
- Schema-level guards on the add-note payload.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.models.enums import AuditAction, UserStatus
from app.schemas.admin_customer import (
    AddCustomerNoteRequest,
    CustomerActivityEventType,
)
from app.services.admin_customer_service import AdminCustomerService


def _user(*, created_at: datetime | None = None) -> MagicMock:
    """Minimal stand-in for a User row — only the attributes the
    pure-logic functions touch."""
    u = MagicMock()
    u.id = uuid.uuid4()
    u.clerk_user_id = "user_test"
    u.email = "x@example.com"
    u.phone = None
    u.status = UserStatus.ACTIVE
    u.created_at = created_at or datetime.now(UTC) - timedelta(days=30)
    u.deleted_at = None
    return u


# ---------- KPI: AOV / no orders -----------------------------------------


def test_kpi_zero_paid_orders_aov_is_zero() -> None:
    user = _user()
    raw = {
        "orders_count": 3,
        "paid_orders_count": 0,
        "cancelled_orders_count": 1,
        "lifetime_value": 0,
        "first_order_at": None,
        "last_order_at": None,
    }
    kpi = AdminCustomerService._compute_kpi(user, raw)
    assert kpi.average_order_value == Decimal("0.00")
    assert kpi.is_repeat is False
    assert kpi.days_since_last_order is None


def test_kpi_single_paid_order_not_repeat() -> None:
    user = _user()
    raw = {
        "orders_count": 1,
        "paid_orders_count": 1,
        "cancelled_orders_count": 0,
        "lifetime_value": Decimal("4500"),
        "first_order_at": datetime.now(UTC) - timedelta(days=5),
        "last_order_at": datetime.now(UTC) - timedelta(days=5),
    }
    kpi = AdminCustomerService._compute_kpi(user, raw)
    assert kpi.average_order_value == Decimal("4500.00")
    assert kpi.is_repeat is False


def test_kpi_repeat_at_two_paid() -> None:
    user = _user()
    raw = {
        "orders_count": 4,
        "paid_orders_count": 2,
        "cancelled_orders_count": 1,
        "lifetime_value": Decimal("9000"),
        "first_order_at": datetime.now(UTC) - timedelta(days=60),
        "last_order_at": datetime.now(UTC) - timedelta(days=3),
    }
    kpi = AdminCustomerService._compute_kpi(user, raw)
    assert kpi.is_repeat is True
    assert kpi.average_order_value == Decimal("4500.00")


# ---------- KPI: AOV rounding --------------------------------------------


def test_kpi_aov_round_half_up_two_dp() -> None:
    """Matches the analytics service convention — finance reporting
    uses HALF_UP, not banker's rounding."""
    user = _user()
    raw = {
        "orders_count": 3,
        "paid_orders_count": 3,
        "cancelled_orders_count": 0,
        "lifetime_value": Decimal("100"),
        "first_order_at": None,
        "last_order_at": None,
    }
    # 100/3 = 33.333... → 33.33
    assert AdminCustomerService._compute_kpi(user, raw).average_order_value == Decimal("33.33")

    raw["lifetime_value"] = Decimal("100")
    raw["paid_orders_count"] = 7
    # 100/7 = 14.2857... → 14.29
    assert AdminCustomerService._compute_kpi(user, raw).average_order_value == Decimal("14.29")


# ---------- KPI: days_since_last_order -----------------------------------


def test_kpi_days_since_naive_datetime_gets_utc_assumed() -> None:
    """A bug elsewhere shouldn't crash us — naive datetimes are treated
    as UTC defensively."""
    user = _user()
    # Construct a naive datetime explicitly (deprecated `utcnow()` would
    # raise under pytest's `filterwarnings=error`).
    naive_last = (datetime.now(UTC) - timedelta(days=10)).replace(tzinfo=None)
    raw = {
        "orders_count": 1,
        "paid_orders_count": 1,
        "cancelled_orders_count": 0,
        "lifetime_value": Decimal("100"),
        "first_order_at": naive_last,
        "last_order_at": naive_last,
    }
    kpi = AdminCustomerService._compute_kpi(user, raw)
    assert kpi.days_since_last_order is not None
    # Allow ±1 day for the test running across midnight UTC.
    assert 9 <= kpi.days_since_last_order <= 11


def test_kpi_days_since_clock_skew_defense() -> None:
    """If the DB hands us a future timestamp (clock skew between Neon
    and the app server), days_since should clamp to 0, not go negative."""
    user = _user()
    future = datetime.now(UTC) + timedelta(days=2)
    raw = {
        "orders_count": 1,
        "paid_orders_count": 1,
        "cancelled_orders_count": 0,
        "lifetime_value": Decimal("100"),
        "first_order_at": future,
        "last_order_at": future,
    }
    kpi = AdminCustomerService._compute_kpi(user, raw)
    assert kpi.days_since_last_order == 0


def test_kpi_account_age_days_basic() -> None:
    user = _user(created_at=datetime.now(UTC) - timedelta(days=45))
    raw = {
        "orders_count": 0,
        "paid_orders_count": 0,
        "cancelled_orders_count": 0,
        "lifetime_value": 0,
        "first_order_at": None,
        "last_order_at": None,
    }
    kpi = AdminCustomerService._compute_kpi(user, raw)
    assert 44 <= kpi.account_age_days <= 46


def test_kpi_account_age_clock_skew_clamps() -> None:
    """If user.created_at comes back as the future (race during signup),
    account_age clamps to 0 instead of being negative."""
    user = _user(created_at=datetime.now(UTC) + timedelta(hours=1))
    raw = {
        "orders_count": 0,
        "paid_orders_count": 0,
        "cancelled_orders_count": 0,
        "lifetime_value": 0,
        "first_order_at": None,
        "last_order_at": None,
    }
    assert AdminCustomerService._compute_kpi(user, raw).account_age_days == 0


# ---------- Audit-log → activity event mapping ---------------------------


def _audit_row(
    *,
    metadata: dict,
    action: AuditAction = AuditAction.UPDATE,
    summary: str = "x",
    actor: uuid.UUID | None = None,
) -> MagicMock:
    row = MagicMock()
    row.created_at = datetime.now(UTC)
    row.action = action
    row.metadata_ = metadata
    row.summary = summary
    row.actor_user_id = actor
    return row


def test_audit_to_event_admin_note_passes_through() -> None:
    evt = AdminCustomerService._audit_to_event(
        _audit_row(metadata={"type": "admin_note", "note": "x"})
    )
    assert evt is not None
    assert evt.type is CustomerActivityEventType.NOTE_ADDED
    assert evt.metadata["note"] == "x"


def test_audit_to_event_other_types_ignored() -> None:
    """Status changes / role assignments are surfaced through OTHER
    sources (orders, user_roles); the audit-row mapper would duplicate."""
    evt = AdminCustomerService._audit_to_event(
        _audit_row(metadata={"type": "shipment_updated"})
    )
    assert evt is None
    evt = AdminCustomerService._audit_to_event(
        _audit_row(metadata={}, action=AuditAction.CREATE)
    )
    assert evt is None


def test_audit_to_event_carries_actor() -> None:
    actor = uuid.uuid4()
    evt = AdminCustomerService._audit_to_event(
        _audit_row(metadata={"type": "admin_note"}, actor=actor)
    )
    assert evt is not None
    assert evt.actor_user_id == actor


# ---------- Schema guards ------------------------------------------------


def test_add_note_min_length() -> None:
    with pytest.raises(PydanticValidationError):
        AddCustomerNoteRequest(note="")


def test_add_note_max_length() -> None:
    with pytest.raises(PydanticValidationError):
        AddCustomerNoteRequest(note="x" * 2001)


def test_add_note_extras_silently_ignored() -> None:
    """`extra='ignore'` keeps an evolving admin UI from breaking the API."""
    body = AddCustomerNoteRequest.model_validate({
        "note": "test", "future_field": "anything",
    })
    assert body.note == "test"


# ---------- order_to_summary defensive item_count ------------------------


def test_order_to_summary_lazy_load_protected() -> None:
    """If `order.items` was never loaded (rare path), `item_count` should
    fall back to 0 rather than triggering an async lazy load."""
    order = MagicMock()
    order.id = uuid.uuid4()
    order.order_number = "OOR-X"
    # Make `.items` raise on access — simulates a non-loaded relationship.
    type(order).items = property(lambda self: (_ for _ in ()).throw(RuntimeError("lazy")))
    order.status = MagicMock(value="placed")
    order.payment_status = MagicMock(value="pending")
    order.total = Decimal("100")
    order.currency = "INR"
    order.placed_at = None
    order.created_at = datetime.now(UTC)

    summary = AdminCustomerService._order_to_summary(order)
    assert summary.item_count == 0
