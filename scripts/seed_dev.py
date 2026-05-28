"""Dev / staging catalog seed CLI (convenience wrapper).

Commands:
    uv run python -m scripts.seed_dev run       # idempotent, non-destructive sync
    uv run python -m scripts.seed_dev status    # show row counts

This wraps the production-safe `MerchandisingSync` (app/seeds/sync.py). There
is NO `reset`/`reseed` any more — retirement is archive/deactivate, never
delete, so order history and referenced rows are always preserved.

Safety:
- This convenience CLI refuses if `APP_ENV=prod`. To apply the canonical
  catalog to a production database (which is safe — the sync is
  non-destructive), use `scripts/sync_catalog.py`, which supports a `--dry-run`
  preview and an explicit prod confirmation.
- Every command prints the resolved DB host before touching anything.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys

from app.core.config import AppEnv, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import (
    build_engine,
    build_sessionmaker,
    dispose_engine,
    session_scope,
    set_db_state,
)
from app.seeds.runner import run_seeds, seed_status


def _sanitize_host(url: str) -> str:
    return re.sub(r"://[^@]+@", "://***:***@", url)


async def _with_db(coro_factory):  # type: ignore[no-untyped-def]
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("scripts.seed_dev")

    engine = build_engine(settings)
    sm = build_sessionmaker(engine)
    set_db_state(engine, sm)

    try:
        async for session in session_scope():
            await coro_factory(session, log)
    finally:
        await dispose_engine()


async def cmd_run() -> None:
    async def _do(session, log):  # type: ignore[no-untyped-def]
        report = await run_seeds(session)
        log.info("merchandising_sync_complete", **dict(report.created))
        print("Merchandising sync complete:")
        for line in report.lines():
            print(line)

    await _with_db(_do)


async def cmd_status() -> None:
    async def _do(session, log):  # type: ignore[no-untyped-def]
        del log
        counts = await seed_status(session)
        print("Catalog row counts:")
        for k in sorted(counts):
            print(f"  {k:30s}  {counts[k]:>5}")

    await _with_db(_do)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="seed_dev",
        description="Dev / staging catalog sync CLI (non-destructive).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Apply the canonical catalog idempotently (sync).")
    sub.add_parser("status", help="Print catalog row counts.")

    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env is AppEnv.PROD:
        print(
            "ERROR: seed_dev refuses APP_ENV=prod. Use `scripts/sync_catalog.py` "
            "(production-safe, supports --dry-run) to sync the prod catalog.",
            file=sys.stderr,
        )
        sys.exit(2)

    print(
        f"[seed_dev] env={settings.app_env.value}  "
        f"db={_sanitize_host(str(settings.database_url))}",
        file=sys.stderr,
    )

    if args.command == "run":
        asyncio.run(cmd_run())
    elif args.command == "status":
        asyncio.run(cmd_status())


if __name__ == "__main__":
    main()
