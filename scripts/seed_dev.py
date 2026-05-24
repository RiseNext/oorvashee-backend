"""Dev / staging seed CLI.

Commands:
    uv run python -m scripts.seed_dev run            # idempotent apply
    uv run python -m scripts.seed_dev reset --yes    # delete seed-owned rows
    uv run python -m scripts.seed_dev reseed --yes   # reset + run
    uv run python -m scripts.seed_dev status         # show row counts

Safety:
- ANY command refuses if `APP_ENV=prod`. Production catalog data is owned by
  the admin dashboard, not this script. If you need a one-time production
  bootstrap (system categories, admin user, T&Cs), build a separate
  `bootstrap_prod.py` — don't repurpose dev seeds.
- `reset` / `reseed` require an explicit `--yes` flag to avoid accidents.
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
from app.seeds.runner import reset_seeds, run_seeds, seed_status


def _sanitize_host(url: str) -> str:
    """Mask user/pass; surface only driver + host + db for the safety banner."""
    return re.sub(r"://[^@]+@", "://***:***@", url)


def _refuse_prod_or_exit(settings: object) -> None:
    if getattr(settings, "app_env", None) is AppEnv.PROD:
        print(
            "ERROR: Refusing to run dev seeds against APP_ENV=prod.",
            file=sys.stderr,
        )
        sys.exit(2)


async def _with_db(coro_factory):  # type: ignore[no-untyped-def]
    """Bring up engine + sessionmaker for the lifetime of one command."""
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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_run() -> None:
    async def _do(session, log):  # type: ignore[no-untyped-def]
        report = await run_seeds(session)
        log.info("seed_complete", **dict(report.created))
        print("Seed complete:")
        for line in report.lines():
            print(line)

    await _with_db(_do)


async def cmd_reset() -> None:
    async def _do(session, log):  # type: ignore[no-untyped-def]
        report = await reset_seeds(session)
        log.warning("seed_reset_complete", deleted=dict(report.updated))
        print("Seed reset complete (rows deleted):")
        for k in sorted(report.updated):
            print(f"  {k:24s}  deleted={report.updated[k]:>3}")

    await _with_db(_do)


async def cmd_reseed() -> None:
    async def _do(session, log):  # type: ignore[no-untyped-def]
        await reset_seeds(session)
        report = await run_seeds(session)
        log.info("seed_reseed_complete")
        print("Reseed complete:")
        for line in report.lines():
            print(line)

    await _with_db(_do)


async def cmd_status() -> None:
    async def _do(session, log):  # type: ignore[no-untyped-def]
        del log
        counts = await seed_status(session)
        print("Seed-owned row counts:")
        for k in sorted(counts):
            print(f"  {k:24s}  {counts[k]:>5}")

    await _with_db(_do)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="seed_dev",
        description="Dev / staging seed CLI for the Oorvashee catalog.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Apply seeds idempotently.")

    p_reset = sub.add_parser(
        "reset", help="Delete every seed-owned row (categories, products, banners)."
    )
    p_reset.add_argument(
        "--yes", action="store_true", help="Confirm the destructive operation.",
    )

    p_reseed = sub.add_parser("reseed", help="Reset then run. Requires --yes.")
    p_reseed.add_argument("--yes", action="store_true")

    sub.add_parser("status", help="Print counts of seed-owned rows in the DB.")

    args = parser.parse_args()

    settings = get_settings()
    _refuse_prod_or_exit(settings)

    print(
        f"[seed_dev] env={settings.app_env.value}  "
        f"db={_sanitize_host(str(settings.database_url))}",
        file=sys.stderr,
    )

    if args.command in {"reset", "reseed"} and not args.yes:
        print(
            f"ERROR: `{args.command}` is destructive. Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.command == "run":
        asyncio.run(cmd_run())
    elif args.command == "reset":
        asyncio.run(cmd_reset())
    elif args.command == "reseed":
        asyncio.run(cmd_reseed())
    elif args.command == "status":
        asyncio.run(cmd_status())


if __name__ == "__main__":
    main()
