"""Clerk webhook endpoint.

Clerk sends Svix-wrapped events with the following critical headers:
  - svix-id
  - svix-timestamp
  - svix-signature

The Svix library handles signature verification AND replay protection
(rejects events with timestamps older than 5 minutes by default).

Per AUTH_FLOW.md §5 we handle:
  - user.created  → insert local user + profile + role
  - user.updated  → reconcile fields + role
  - user.deleted  → soft-delete + PII scrub

Other event types (organisation events, session events) are logged and
ignored — return 200 quickly so Svix doesn't retry.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response

from app.core.config import get_settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.core.rate_limit import profiles
from app.db.deps import DbSession
from app.integrations.clerk import verify_clerk_webhook
from app.schemas.clerk_webhook import ClerkUser
from app.services.user_sync_service import UserSyncService

router = APIRouter()
log = get_logger(__name__)


_HANDLED_EVENTS = {"user.created", "user.updated", "user.deleted"}


@router.post(
    "/clerk",
    summary="Clerk webhook (Svix-signed, replay-protected)",
)
@profiles.webhook()
async def clerk_webhook(request: Request, response: Response, session: DbSession) -> dict[str, str]:
    settings = get_settings()
    raw = await request.body()
    if not raw:
        raise IntegrationError("Empty Clerk webhook body", code="clerk_empty_body")

    # Svix library accepts any dict-like; lower-casing keys is defensive
    # against header-case quirks across reverse proxies.
    headers = {k.lower(): v for k, v in request.headers.items()}
    verify_clerk_webhook(
        payload=raw,
        headers=headers,
        signing_secret=settings.clerk_webhook_secret,
    )

    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationError("Invalid JSON in Clerk webhook body") from exc

    event_type = event.get("type")
    data = event.get("data") or {}

    if event_type not in _HANDLED_EVENTS:
        log.info("clerk_webhook_ignored", type=event_type)
        return {"status": "ignored", "type": str(event_type)}

    sync = UserSyncService(session)

    if event_type in {"user.created", "user.updated"}:
        try:
            clerk_user = ClerkUser.model_validate(data)
        except Exception as exc:  # pragma: no cover — Pydantic raises ValidationError
            raise IntegrationError(
                "Clerk user payload validation failed",
                code="clerk_payload_invalid",
            ) from exc

        if event_type == "user.created":
            await sync.handle_user_created(clerk_user)
        else:
            await sync.handle_user_updated(clerk_user)
        return {"status": "ok", "type": event_type}

    # user.deleted — Clerk only sends `{id, deleted}` here.
    clerk_user_id = data.get("id")
    if not clerk_user_id:
        raise IntegrationError(
            "Clerk user.deleted payload missing id",
            code="clerk_payload_invalid",
        )
    await sync.handle_user_deleted(str(clerk_user_id))
    return {"status": "ok", "type": event_type}
