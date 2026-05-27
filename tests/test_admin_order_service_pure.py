"""Pure-logic tests for AdminOrderService — no DB.

Covers:
- Full status transition matrix (valid + invalid moves).
- Terminal-state immutability.
- Schema-level field guards (cancel reason min length, note min length).
- Allowed-targets introspection helper.
- Audit-log → timeline event mapping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ConflictError
from app.models.enums import AuditAction, OrderStatus
from app.schemas.admin_order import (
    AddOrderNoteRequest,
    CancelOrderRequest,
    OrderTimelineEventType,
    ShipmentUpdateRequest,
)
from app.services.admin_order_service import AdminOrderService

# ---------- Transition matrix --------------------------------------------


VALID_TRANSITIONS = {
    (OrderStatus.PLACED, OrderStatus.PACKED),
    (OrderStatus.PLACED, OrderStatus.CANCELLED),
    (OrderStatus.PACKED, OrderStatus.SHIPPED),
    (OrderStatus.PACKED, OrderStatus.CANCELLED),
    (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
    (OrderStatus.SHIPPED, OrderStatus.CANCELLED),
}


@pytest.mark.parametrize("current,target", sorted(VALID_TRANSITIONS))
def test_valid_transitions_pass(
    current: OrderStatus, target: OrderStatus
) -> None:
    AdminOrderService._assert_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        # Skip-ahead moves are rejected (must go through each state).
        (OrderStatus.PLACED, OrderStatus.SHIPPED),
        (OrderStatus.PLACED, OrderStatus.DELIVERED),
        (OrderStatus.PACKED, OrderStatus.DELIVERED),
        # Backwards moves are rejected.
        (OrderStatus.PACKED, OrderStatus.PLACED),
        (OrderStatus.SHIPPED, OrderStatus.PACKED),
        (OrderStatus.SHIPPED, OrderStatus.PLACED),
        # Terminal states reject everything.
        (OrderStatus.DELIVERED, OrderStatus.PACKED),
        (OrderStatus.DELIVERED, OrderStatus.SHIPPED),
        (OrderStatus.DELIVERED, OrderStatus.CANCELLED),
        (OrderStatus.CANCELLED, OrderStatus.PLACED),
        (OrderStatus.CANCELLED, OrderStatus.PACKED),
    ],
)
def test_invalid_transitions_rejected(
    current: OrderStatus, target: OrderStatus
) -> None:
    with pytest.raises(ConflictError) as exc:
        AdminOrderService._assert_transition(current, target)
    assert exc.value.code == "invalid_status_transition"


def test_terminal_states_carry_clear_extra() -> None:
    """The error extras carry both states so the admin UI can render
    "Order is in terminal state X — cannot move to Y" without guessing."""
    with pytest.raises(ConflictError) as exc:
        AdminOrderService._assert_transition(
            OrderStatus.DELIVERED, OrderStatus.CANCELLED
        )
    assert exc.value.extra["current"] == OrderStatus.DELIVERED.value
    assert exc.value.extra["target"] == OrderStatus.CANCELLED.value


# ---------- Allowed-targets helper ---------------------------------------


@pytest.mark.parametrize(
    "current,expected_count",
    [
        (OrderStatus.PLACED, 2),
        (OrderStatus.PACKED, 2),
        (OrderStatus.SHIPPED, 2),
        (OrderStatus.DELIVERED, 0),
        (OrderStatus.CANCELLED, 0),
    ],
)
def test_list_allowed_targets_shape(
    current: OrderStatus, expected_count: int
) -> None:
    targets = AdminOrderService.list_allowed_targets(current)
    assert len(list(targets)) == expected_count


def test_allowed_targets_matrix_matches_assert() -> None:
    """The introspection helper and the runtime assert must agree —
    otherwise a future admin UI would show buttons that 422 on click."""
    for current in OrderStatus:
        allowed = set(AdminOrderService.list_allowed_targets(current))
        for target in OrderStatus:
            if target in allowed:
                # Asserting must not raise for allowed moves.
                AdminOrderService._assert_transition(current, target)
            elif current == target:
                # Idempotent moves are handled BEFORE assert is called.
                continue
            else:
                with pytest.raises(ConflictError):
                    AdminOrderService._assert_transition(current, target)


# ---------- Schema guards ------------------------------------------------


def test_cancel_reason_required_non_empty() -> None:
    with pytest.raises(PydanticValidationError):
        CancelOrderRequest(reason="")
    with pytest.raises(PydanticValidationError):
        CancelOrderRequest(reason="hi")  # below min_length=3


def test_cancel_reason_max_length() -> None:
    with pytest.raises(PydanticValidationError):
        CancelOrderRequest(reason="x" * 501)


def test_cancel_request_notify_defaults_false() -> None:
    body = CancelOrderRequest(reason="customer changed mind")
    assert body.notify_customer is False


def test_note_min_max_length() -> None:
    with pytest.raises(PydanticValidationError):
        AddOrderNoteRequest(note="")
    with pytest.raises(PydanticValidationError):
        AddOrderNoteRequest(note="x" * 2001)
    body = AddOrderNoteRequest(note="lalita called, ship after Diwali")
    assert "lalita" in body.note


def test_shipment_update_all_optional() -> None:
    """For `update_shipment` — admin can patch only the field that needs
    fixing. mark_shipped enforces required-ness at the service layer."""
    body = ShipmentUpdateRequest()
    assert body.courier_name is None
    assert body.tracking_id is None
    assert body.tracking_url is None


def test_shipment_update_field_length_caps() -> None:
    with pytest.raises(PydanticValidationError):
        ShipmentUpdateRequest(courier_name="x" * 121)
    with pytest.raises(PydanticValidationError):
        ShipmentUpdateRequest(tracking_id="x" * 121)
    with pytest.raises(PydanticValidationError):
        ShipmentUpdateRequest(tracking_url="x" * 2049)


# ---------- Audit-log → timeline event mapping ---------------------------


def _audit(
    *,
    action: AuditAction,
    metadata: dict,
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


def test_audit_to_timeline_admin_note() -> None:
    evt = AdminOrderService._audit_to_timeline(
        _audit(action=AuditAction.UPDATE, metadata={"type": "admin_note", "note": "x"})
    )
    assert evt is not None
    assert evt.type is OrderTimelineEventType.NOTE_ADDED


def test_audit_to_timeline_shipment_update() -> None:
    evt = AdminOrderService._audit_to_timeline(
        _audit(
            action=AuditAction.UPDATE,
            metadata={
                "type": "shipment_updated",
                "changed": {"tracking_id": {"from": None, "to": "X"}},
            },
        )
    )
    assert evt is not None
    assert evt.type is OrderTimelineEventType.SHIPMENT_UPDATED


def test_audit_to_timeline_cod_paid() -> None:
    evt = AdminOrderService._audit_to_timeline(
        _audit(
            action=AuditAction.PAYMENT_CAPTURED,
            metadata={"payment_method": "cod"},
        )
    )
    assert evt is not None
    assert evt.type is OrderTimelineEventType.COD_PAID


def test_audit_to_timeline_skips_status_transitions() -> None:
    """Status-change audit rows are deliberately skipped — the canonical
    timeline events come from the order's timestamp columns, not from
    audit_logs, so we don't duplicate."""
    evt = AdminOrderService._audit_to_timeline(
        _audit(
            action=AuditAction.STATUS_CHANGED,
            metadata={"from": "placed", "to": "packed"},
        )
    )
    assert evt is None


def test_audit_to_timeline_skips_unknown_actions() -> None:
    evt = AdminOrderService._audit_to_timeline(
        _audit(action=AuditAction.CREATE, metadata={})
    )
    assert evt is None
