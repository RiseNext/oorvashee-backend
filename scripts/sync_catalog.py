"""Production-safe canonical catalog (merchandising) sync CLI.

Reconciles the canonical catalog into the target database using
`MerchandisingSync` — an idempotent, **non-destructive** operation (upsert +
archive/deactivate; never delete/truncate). Safe to run against a production
database with live order history.

Commands:
    uv run python -m scripts.sync_catalog sync --dry-run   # preview, rolls back
    uv run python -m scripts.sync_catalog sync             # apply (non-prod)
    uv run python -m scripts.sync_catalog sync --yes       # apply on APP_ENV=prod
    uv run python -m scripts.sync_catalog status           # row counts

- `--dry-run` runs the full sync in a transaction then ROLLS BACK, printing
  what would change. Use it to preview against prod safely.
- Applying against `APP_ENV=prod` requires `--yes` (the sync is safe, but
  catalog writes to prod should be intentional).
- The resolved DB host is always printed first.
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
    set_db_state,
)
from app.seeds.runner import seed_status
from app.seeds.sync import MerchandisingSync


def _sanitize_host(url: str) -> str:
    return re.sub(r"://[^@]+@", "://***:***@", url)


async def _sync(dry_run: bool) -> None:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("scripts.sync_catalog")

    engine = build_engine(settings)
    sm = build_sessionmaker(engine)
    set_db_state(engine, sm)
    try:
        session = sm()
        try:
            report = await MerchandisingSync(session).sync()
            if dry_run:
                await session.rollback()
                print("DRY RUN — no changes committed. Would apply:")
            else:
                await session.commit()
                log.info("catalog_sync_applied", **dict(report.created))
                print("Catalog sync applied:")
            for line in report.lines():
                print(line)
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
    finally:
        await dispose_engine()


async def _status() -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = build_engine(settings)
    sm = build_sessionmaker(engine)
    set_db_state(engine, sm)
    try:
        session = sm()
        try:
            counts = await seed_status(session)
            print("Catalog row counts:")
            for k in sorted(counts):
                print(f"  {k:30s}  {counts[k]:>5}")
        finally:
            await session.close()
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sync_catalog",
        description="Production-safe canonical catalog merchandising sync.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_sync = sub.add_parser("sync", help="Reconcile the canonical catalog (safe/idempotent).")
    p_sync.add_argument("--dry-run", action="store_true", help="Preview only; roll back.")
    p_sync.add_argument("--yes", action="store_true", help="Required to apply on APP_ENV=prod.")
    sub.add_parser("status", help="Print catalog row counts.")

    args = parser.parse_args()
    settings = get_settings()

    print(
        f"[sync_catalog] env={settings.app_env.value}  "
        f"db={_sanitize_host(str(settings.database_url))}",
        file=sys.stderr,
    )

    if args.command == "status":
        asyncio.run(_status())
        return

    # command == "sync"
    is_prod = settings.app_env is AppEnv.PROD
    if is_prod and not args.dry_run and not args.yes:
        print(
            "ERROR: applying the catalog sync on APP_ENV=prod requires --yes "
            "(the operation is non-destructive, but prod writes should be "
            "intentional). Tip: run with --dry-run first to preview.",
            file=sys.stderr,
        )
        sys.exit(2)

    asyncio.run(_sync(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
