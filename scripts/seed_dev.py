"""Seed local dev data.

Cycle 1+ will add seed Products / Variants / Categories here. For now this is
a no-op stub that proves the engine can be acquired in a script context.

Run:
    uv run python -m scripts.seed_dev
"""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import build_engine, build_sessionmaker, set_db_state


async def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("scripts.seed_dev")

    engine = build_engine(settings)
    sessionmaker = build_sessionmaker(engine)
    set_db_state(engine, sessionmaker)

    log.info("seed_noop", note="No models registered yet — nothing to seed.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
