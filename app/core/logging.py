"""Structured logging setup using structlog.

- One log line per event, JSON in non-local environments.
- `request_id` is bound per request by middleware and propagates into every log.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import LogLevel, Settings


def _drop_color_message_key(_: Any, __: str, event_dict: EventDict) -> EventDict:
    """Uvicorn duplicates msg into `color_message`. Strip it."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Idempotently configure stdlib + structlog. Call once at app startup."""
    level = logging.getLevelName(settings.log_level.value)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        _drop_color_message_key,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.log_json:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Clear any handlers Uvicorn or pytest may have attached
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy third-party loggers
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True

    # SQLAlchemy echo is controlled by engine config, not stdlib level
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Convenience accessor for module-scoped loggers."""
    return structlog.get_logger(name)


# ---------- Sensitive header scrubbing ----------

_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "x-bot-token",
        "x-razorpay-signature",
        "svix-signature",
    }
)


def scrub_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of headers with sensitive values masked. Use before logging."""
    return {
        k: ("***" if k.lower() in _SENSITIVE_HEADERS else v) for k, v in headers.items()
    }


_LOG_LEVEL_NAMES = {level.value for level in LogLevel}


def is_valid_log_level(name: str) -> bool:
    return name.upper() in _LOG_LEVEL_NAMES
