"""Delete stale `request_idempotency` rows.

Cycle 2D introduced the `request_idempotency` table for the
`Idempotency-Key` header on `/checkout/orders` and `/payments/verify`.
Rows live for 24h by default; this script enforces that retention.

Designed for Railway Cron — a daily one-shot is enough. The DELETE is
batched so a sudden burst (e.g. table grew unexpectedly) doesn't hold
a long lock against the live API.

Usage:
    uv run python -m scripts.cleanup_idempotency
    uv run python -m scripts.cleanup_idempotency --older-than-hours 24 --batch 1000

Exit codes:
    0 — success (regardless of how many rows were deleted, including zero)
    1 — unexpected error
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import (
    build_engine,
    build_sessionmaker,
    dispose_engine,
    session_scope,
    set_db_state,
)

DEFAULT_OLDER_THAN_HOURS = 24
DEFAULT_BATCH_SIZE = 500
MAX_TOTAL_DELETES = 1_000_000  # safety brake against a runaway loop


async def _cleanup_once(older_than_hours: int, batch_size: int) -> int:
    log = get_logger("scripts.cleanup_idempotency")
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    total_deleted = 0

    async for session in session_scope():
        while total_deleted < MAX_TOTAL_DELETES:
            # Postgres has no `DELETE ... LIMIT`; emulate via a subquery
            # so we cap lock duration per batch.
            result = await session.execute(
                text(
                    """
                    DELETE FROM request_idempotency
                    WHERE id IN (
                        SELECT id FROM request_idempotency
                        WHERE created_at < :cutoff
                        ORDER BY created_at ASC
                        LIMIT :batch
                    )
                    """
                ),
                {"cutoff": cutoff, "batch": batch_size},
            )
            deleted = int(result.rowcount or 0)
            total_deleted += deleted
            if deleted < batch_size:
                # Last batch — table caught up.
                break
            await session.commit()  # release locks between batches

    log.info(
        "idempotency_cleanup_complete",
        deleted=total_deleted,
        cutoff=cutoff.isoformat(),
        older_than_hours=older_than_hours,
        batch_size=batch_size,
    )
    return total_deleted


async def _run(older_than_hours: int, batch_size: int) -> int:
    settings = get_settings()
    configure_logging(settings)

    engine = build_engine(settings)
    sm = build_sessionmaker(engine)
    set_db_state(engine, sm)

    try:
        return await _cleanup_once(older_than_hours, batch_size)
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cleanup_idempotency",
        description="Delete request_idempotency rows older than N hours.",
    )
    parser.add_argument(
        "--older-than-hours", type=int, default=DEFAULT_OLDER_THAN_HOURS,
        help=f"Retention window (default: {DEFAULT_OLDER_THAN_HOURS})",
    )
    parser.add_argument(
        "--batch", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Rows per DELETE batch (default: {DEFAULT_BATCH_SIZE})",
    )
    args = parser.parse_args()

    if args.older_than_hours < 1:
        print("ERROR: --older-than-hours must be >= 1", file=sys.stderr)
        sys.exit(2)
    if args.batch < 1 or args.batch > 10_000:
        print("ERROR: --batch must be between 1 and 10000", file=sys.stderr)
        sys.exit(2)

    try:
        deleted = asyncio.run(_run(args.older_than_hours, args.batch))
    except Exception as exc:
        print(f"ERROR: cleanup failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"OK — deleted {deleted} row(s) older than {args.older_than_hours}h")


if __name__ == "__main__":
    main()
