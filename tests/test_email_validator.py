"""Unit tests for app.core.validators.is_plausible_email.

Guards the auth provisioning + Clerk webhook against poisoning
`users.email` with blank or template-literal values.
"""

from __future__ import annotations

import pytest

from app.core.validators import is_plausible_email


@pytest.mark.parametrize(
    "value",
    [
        "user@example.com",
        "first.last@sub.domain.co.in",
        "  spaced@example.com  ",  # trimmed before checking
        "a+tag@example.org",
    ],
)
def test_accepts_real_emails(value: str) -> None:
    assert is_plausible_email(value) is True


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "not-an-email",
        "missing-domain@",
        "@missing-local.com",
        "no-tld@localhost",
        # The exact production poison value + close variants:
        "{{user.primary_email_address.email_address}}",
        "{{user.primary_email_address}}",
        "user@{{domain}}",
    ],
)
def test_rejects_blank_and_template_literals(value: str | None) -> None:
    assert is_plausible_email(value) is False
