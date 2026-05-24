"""Razorpay HMAC signature verification — pure crypto, no network."""

from __future__ import annotations

import hmac
from hashlib import sha256
from unittest.mock import MagicMock

import pytest

from app.integrations.razorpay_client import RazorpayClient, inr_to_paise


def _fake_settings(secret: str = "test_secret_xyz") -> MagicMock:
    settings = MagicMock()
    settings.razorpay_key_id = "rzp_test_demo"
    settings.razorpay_key_secret = secret
    settings.razorpay_webhook_secret = "webhook_" + secret
    return settings


def test_inr_to_paise_basic() -> None:
    from decimal import Decimal

    assert inr_to_paise(Decimal("100.00")) == 10000
    assert inr_to_paise(Decimal("1.50")) == 150
    assert inr_to_paise(Decimal("99.99")) == 9999


def test_inr_to_paise_rejects_below_one_rupee() -> None:
    from decimal import Decimal

    from app.core.exceptions import IntegrationError

    with pytest.raises(IntegrationError):
        inr_to_paise(Decimal("0.50"))


def test_verify_payment_signature_accepts_valid() -> None:
    client = RazorpayClient(_fake_settings())
    order_id = "order_ABC123"
    payment_id = "pay_XYZ789"
    msg = f"{order_id}|{payment_id}".encode()
    expected_sig = hmac.new(b"test_secret_xyz", msg, sha256).hexdigest()

    assert client.verify_payment_signature(
        razorpay_order_id=order_id,
        razorpay_payment_id=payment_id,
        razorpay_signature=expected_sig,
    )


def test_verify_payment_signature_rejects_tamper() -> None:
    client = RazorpayClient(_fake_settings())
    assert not client.verify_payment_signature(
        razorpay_order_id="order_ABC123",
        razorpay_payment_id="pay_XYZ789",
        razorpay_signature="deadbeef" * 8,
    )


def test_verify_webhook_signature_accepts_valid() -> None:
    client = RazorpayClient(_fake_settings())
    body = b'{"event":"payment.captured","payload":{}}'
    sig = hmac.new(b"webhook_test_secret_xyz", body, sha256).hexdigest()
    assert client.verify_webhook_signature(payload=body, signature=sig)


def test_verify_webhook_signature_rejects_wrong_secret() -> None:
    client = RazorpayClient(_fake_settings())
    body = b'{"event":"payment.captured"}'
    # Signed with the API secret instead of the webhook secret — wrong key.
    bad_sig = hmac.new(b"test_secret_xyz", body, sha256).hexdigest()
    assert not client.verify_webhook_signature(payload=body, signature=bad_sig)


def test_signature_uses_constant_time_compare() -> None:
    """Defence against timing attacks: hmac.compare_digest is what we call.

    Sanity check that the implementation lives in this code path so a
    future refactor can't quietly regress to a plain `==`.
    """
    import inspect

    source = inspect.getsource(RazorpayClient.verify_payment_signature)
    assert "compare_digest" in source
