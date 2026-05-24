"""Clerk integration surface.

Cycle 0 only exposes the webhook signature verifier. JWT verification lives in
`app.core.security` because it sits on the request-auth hot path.
"""

from __future__ import annotations

from svix.webhooks import Webhook, WebhookVerificationError

from app.core.exceptions import AuthenticationError


def verify_clerk_webhook(
    *,
    payload: bytes,
    headers: dict[str, str],
    signing_secret: str,
) -> None:
    """Verify a Clerk webhook payload via Svix headers.

    Raises AuthenticationError on any signature or timestamp issue.
    Per AUTH_FLOW.md §9: rejects events older than 5 minutes (Svix default).
    """
    try:
        wh = Webhook(signing_secret)
        wh.verify(payload, headers)
    except WebhookVerificationError as exc:
        raise AuthenticationError("Invalid Clerk webhook signature") from exc
