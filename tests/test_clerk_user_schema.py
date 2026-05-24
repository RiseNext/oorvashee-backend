"""ClerkUser payload helpers — primary email / phone / role derivation."""

from __future__ import annotations

import pytest

from app.schemas.clerk_webhook import ClerkUser


def _user(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "id": "user_2abc",
        "email_addresses": [],
        "phone_numbers": [],
        "public_metadata": {},
    }
    base.update(overrides)
    return ClerkUser.model_validate(base)


def test_primary_email_prefers_marked_id() -> None:
    u = _user(
        email_addresses=[
            {"id": "e1", "email_address": "first@example.com"},
            {"id": "e2", "email_address": "primary@example.com"},
        ],
        primary_email_address_id="e2",
    )
    assert u.primary_email == "primary@example.com"


def test_primary_email_falls_back_to_first_when_no_marker() -> None:
    u = _user(
        email_addresses=[
            {"id": "e1", "email_address": "only@example.com"},
        ],
    )
    assert u.primary_email == "only@example.com"


def test_primary_email_none_when_list_empty() -> None:
    assert _user().primary_email is None


def test_primary_phone_prefers_marked_id() -> None:
    u = _user(
        phone_numbers=[
            {"id": "p1", "phone_number": "+91 9000000001"},
            {"id": "p2", "phone_number": "+91 9000000002"},
        ],
        primary_phone_number_id="p2",
    )
    assert u.primary_phone == "+91 9000000002"


def test_primary_phone_falls_back_to_first() -> None:
    u = _user(
        phone_numbers=[{"id": "p1", "phone_number": "+91 9876543210"}],
    )
    assert u.primary_phone == "+91 9876543210"


@pytest.mark.parametrize(
    "first,last,expected",
    [
        ("Ravi", "Kumar", "Ravi Kumar"),
        ("Ravi", None, "Ravi"),
        (None, "Kumar", "Kumar"),
        (None, None, None),
        ("", "", None),
    ],
)
def test_full_name_combinations(first, last, expected) -> None:  # type: ignore[no-untyped-def]
    u = _user(first_name=first, last_name=last)
    assert u.full_name == expected


def test_role_defaults_to_customer() -> None:
    assert _user().role == "customer"


def test_role_reads_public_metadata() -> None:
    u = _user(public_metadata={"role": "admin"})
    assert u.role == "admin"


def test_role_normalises_case_and_whitespace() -> None:
    u = _user(public_metadata={"role": "  Staff  "})
    assert u.role == "staff"


def test_role_ignores_falsy_value() -> None:
    u = _user(public_metadata={"role": ""})
    assert u.role == "customer"


def test_unknown_fields_silently_ignored() -> None:
    # Clerk adds fields over time; the model must not break on them.
    u = ClerkUser.model_validate({
        "id": "user_x",
        "email_addresses": [],
        "phone_numbers": [],
        "public_metadata": {},
        "future_field": "anything",
        "organization_memberships": [{"id": "org_1"}],
    })
    assert u.id == "user_x"
