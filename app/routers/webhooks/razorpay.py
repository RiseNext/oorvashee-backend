"""Razorpay webhook endpoint.

Razorpay POSTs with `X-Razorpay-Signature` header. We:
1. Read the raw body (signature is HMAC over the exact bytes).
2. Verify the signature using the WEBHOOK secret (different from the API secret).
3. Hand off to PaymentService.handle_webhook.
4. Return 200 quickly so Razorpay doesn't keep retrying.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response

from app.core.config import get_settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.core.rate_limit import profiles
from app.db.deps import DbSession
from app.integrations.razorpay_client import RazorpayClient
from app.services.payment_service import PaymentService

router = APIRouter()
log = get_logger(__name__)


@router.post(
    "/razorpay",
    summary="Razorpay webhook receiver (HMAC-verified, replay-safe)",
)
@profiles.webhook()
async def razorpay_webhook(
    request: Request,
    response: Response,
    session: DbSession,
    x_razorpay_signature: Annotated[
        str | None,
        Header(alias="X-Razorpay-Signature", convert_underscores=False),
    ] = None,
) -> dict[str, str]:
    raw_body = await request.body()
    if not raw_body:
        raise IntegrationError("Empty webhook body", code="razorpay_empty_body")

    settings = get_settings()
    client = RazorpayClient(settings)
    signature_ok = bool(
        x_razorpay_signature
        and client.verify_webhook_signature(
            payload=raw_body, signature=x_razorpay_signature
        )
    )
    if not signature_ok:
        log.warning(
            "razorpay_webhook_bad_signature",
            client=request.client.host if request.client else None,
        )

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationError("Invalid JSON in webhook body") from exc

    return await PaymentService(session).handle_webhook(
        event=event, signature_ok=signature_ok
    )
