"""Thin async wrapper over the Razorpay HTTP API.

We do NOT use the official `razorpay` Python SDK:
- It's sync-only; calling it inside an async service either blocks the
  event loop or forces an `asyncio.to_thread` per call.
- The two endpoints we need (`POST /orders`, signature verify) are tiny
  and well-documented.

Endpoints used here:
- POST https://api.razorpay.com/v1/orders  (create order)
- (signature verification is pure HMAC — no API call)

Webhook signature verification is also implemented here so that all
Razorpay-specific cryptography lives in one module.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger

log = get_logger(__name__)

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
RAZORPAY_TIMEOUT_SECONDS = 8.0


def inr_to_paise(amount: Decimal) -> int:
    """Razorpay wants amounts in paise (1 INR = 100 paise)."""
    # Quantize to two decimals so floating noise doesn't leak in.
    paise = int((amount * 100).quantize(Decimal("1")))
    if paise < 100:
        # Razorpay rejects amounts below ₹1.00 outright.
        raise IntegrationError(
            "Razorpay order amount must be at least ₹1.00",
            extra={"amount_paise": paise},
        )
    return paise


@dataclass(frozen=True, slots=True)
class RazorpayOrder:
    id: str
    amount_paise: int
    currency: str


class RazorpayClient:
    """Stateless client. Instantiate per service call; cheap."""

    def __init__(self, settings: Settings) -> None:
        self._key_id = settings.razorpay_key_id
        self._key_secret = settings.razorpay_key_secret
        self._webhook_secret = settings.razorpay_webhook_secret

    # ---------- Order creation ----------

    async def create_order(
        self,
        *,
        amount: Decimal,
        currency: str,
        receipt: str,
        notes: dict[str, str] | None = None,
    ) -> RazorpayOrder:
        body = {
            "amount": inr_to_paise(amount),
            "currency": currency,
            "receipt": receipt,
            "payment_capture": 1,  # auto-capture on authorisation
        }
        if notes:
            body["notes"] = notes

        try:
            async with httpx.AsyncClient(
                base_url=RAZORPAY_API_BASE,
                auth=(self._key_id, self._key_secret),
                timeout=RAZORPAY_TIMEOUT_SECONDS,
            ) as client:
                response = await client.post("/orders", json=body)
        except httpx.RequestError as exc:
            log.exception("razorpay_request_error", receipt=receipt)
            raise IntegrationError(
                "Razorpay request failed", code="razorpay_unreachable"
            ) from exc

        if response.status_code >= 400:
            log.warning(
                "razorpay_order_create_failed",
                status=response.status_code,
                body=response.text[:500],
                receipt=receipt,
            )
            raise IntegrationError(
                "Razorpay order creation failed",
                code="razorpay_error",
                extra={"upstream_status": response.status_code},
            )

        data: dict[str, Any] = response.json()
        log.info("razorpay_order_created", razorpay_order_id=data["id"], receipt=receipt)
        return RazorpayOrder(
            id=data["id"],
            amount_paise=int(data["amount"]),
            currency=data["currency"],
        )

    @property
    def key_id(self) -> str:
        """Exposed so handoff payloads can include it for the JS widget."""
        return self._key_id

    # ---------- Signature verification ----------

    def verify_payment_signature(
        self,
        *,
        razorpay_order_id: str,
        razorpay_payment_id: str,
        razorpay_signature: str,
    ) -> bool:
        """HMAC-SHA256 over `<razorpay_order_id>|<razorpay_payment_id>`.

        See: https://razorpay.com/docs/payments/server-integration/python/payment-gateway/build-integration/#step-3-verify-signature
        """
        message = f"{razorpay_order_id}|{razorpay_payment_id}".encode()
        expected = hmac.new(
            self._key_secret.encode(), message, sha256
        ).hexdigest()
        return hmac.compare_digest(expected, razorpay_signature)

    def verify_webhook_signature(
        self, *, payload: bytes, signature: str
    ) -> bool:
        """HMAC-SHA256 over the raw request body using the WEBHOOK secret.

        Razorpay sends the signature in the `X-Razorpay-Signature` header.
        """
        expected = hmac.new(
            self._webhook_secret.encode(), payload, sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
