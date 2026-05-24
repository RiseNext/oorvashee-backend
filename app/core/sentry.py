"""Sentry integration — async-safe, optional, no-op when DSN unset.

The `sentry_sdk` package is a runtime dependency (declared in pyproject)
but we still guard the import: a missing package should disable error
reporting silently rather than break the app boot.

What we send:
- All unhandled exceptions (via FastAPI/Starlette integration).
- Distributed traces at `SENTRY_TRACES_SAMPLE_RATE` (10% by default).
- Performance for HTTP requests via the Starlette integration.

What we DO NOT send:
- PII — `send_default_pii=False` strips email/IP from event payloads.
  Our middleware tags add `request_id` deliberately so events stay
  traceable to a log line without exposing customers.
- Cookies / Authorization headers (`max_request_body_size="small"`).

Per-request tags (`request_id`) are bound by RequestContextMiddleware
so every event in that request's scope carries the correlation key.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

_initialised = False


def init_sentry(settings: Settings) -> None:
    """Initialise the SDK if a DSN is configured. Idempotent.

    Defensive against the common `.env` foot-gun: pydantic-settings does
    NOT strip inline `# comments`, so a line like `SENTRY_DSN=# disabled`
    becomes a literal garbage value. We require an http(s) scheme to
    consider the DSN valid; anything else is treated as "no DSN".
    """
    global _initialised
    if _initialised:
        return
    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        log.info("sentry_disabled", reason="no_dsn_configured")
        _initialised = True  # avoid re-checking every restart attempt
        return
    if not (dsn.startswith("https://") or dsn.startswith("http://")):
        log.warning(
            "sentry_dsn_invalid_skipping",
            note="DSN does not start with http(s)://; treating as unset",
        )
        _initialised = True
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning(
            "sentry_sdk_missing",
            note="DSN configured but sentry-sdk import failed; "
            "run `uv sync` to install runtime deps",
        )
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env.value,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=False,  # never auto-include emails/IPs/etc.
        max_request_body_size="small",  # cap captured request bodies
        attach_stacktrace=True,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
    )
    _initialised = True
    log.info(
        "sentry_initialised",
        environment=settings.app_env.value,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )


def set_request_tag(request_id: str | None) -> None:
    """Bind the per-request correlation id onto the active Sentry scope.

    Cheap no-op when Sentry isn't initialised or installed — the SDK's
    `set_tag` is safe to call on an inactive client.
    """
    if not request_id:
        return
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.set_tag("request_id", request_id)
