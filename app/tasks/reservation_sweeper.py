"""In-process reservation expiry sweeper (Phase 4 cron).

Runs the two reservation-lifecycle sweeps on a fixed interval:
  - sweep_expired_reservations()        RESERVED + expires_at<now -> EXPIRED (3-min rule)
  - sweep_payment_processing_backstop() PAYMENT_PROCESSING + expires_at<now -> CANCELLED (15-min)

Single-runner across gunicorn workers via a TRANSACTION-SCOPED Postgres
advisory lock (`pg_try_advisory_xact_lock`). Only one worker wins the lock per
tick; the others skip. The lock auto-releases when the sweep transaction
commits/rolls back, so a crashed worker can never leak it (unlike a
session-level `pg_advisory_lock`).

No external scheduler/dependency: a plain asyncio task started in the app
lifespan. Sleep is interruptible by the stop event for instant shutdown.

The sweeps are also a lazy-safety backstop — every availability read and the
reserve query already exclude `expires_at < now()`, so even between ticks
there is zero overselling window. This loop is what flips the rows to their
terminal status (so admin views + the partial unique index stay truthful).
"""

from __future__ import annotations

import asyncio
import contextlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.services.reservation_lifecycle import ReservationLifecycleService

log = get_logger(__name__)

# Arbitrary, stable 64-bit key shared by every worker so they contend on the
# SAME advisory lock. Changing it would let two workers sweep concurrently.
RESERVATION_SWEEP_LOCK_KEY = 4_872_011_004


async def run_sweep_once(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    lock_key: int = RESERVATION_SWEEP_LOCK_KEY,
) -> dict[str, dict[str, int]] | None:
    """Run one sweep tick under the advisory lock.

    Returns the sweep counts if this caller won the lock, or None if another
    worker is the leader for this tick. The advisory lock + both sweeps share
    one transaction; commit releases the lock.
    """
    async with sessionmaker() as session:
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": lock_key}
            )
        ).scalar_one()
        if not got:
            await session.rollback()
            return None

        lifecycle = ReservationLifecycleService(session)
        expired = await lifecycle.sweep_expired_reservations()
        backstop = await lifecycle.sweep_payment_processing_backstop()
        await session.commit()
        return {"expired": expired, "backstop": backstop}


async def _sweeper_loop(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    interval: float,
    stop_event: asyncio.Event,
    lock_key: int,
) -> None:
    log.info("reservation_sweeper_started", interval_seconds=interval)
    while not stop_event.is_set():
        try:
            result = await run_sweep_once(sessionmaker, lock_key=lock_key)
            if result is not None and _has_work(result):
                log.info(
                    "reservation_sweep_tick",
                    expired_reservations=result["expired"]["reservations"],
                    expired_sessions=result["expired"]["sessions"],
                    backstop_reservations=result["backstop"]["reservations"],
                    backstop_sessions=result["backstop"]["sessions"],
                )
        except Exception:  # never let one bad tick kill the loop
            log.exception("reservation_sweeper_tick_failed")

        # Sleep `interval`, but wake immediately on shutdown.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)

    log.info("reservation_sweeper_stopped")


def _has_work(result: dict[str, dict[str, int]]) -> bool:
    return any(v for bucket in result.values() for v in bucket.values())


class ReservationSweeper:
    """Lifecycle handle for the sweeper task — start in lifespan, stop on shutdown."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        interval: float,
        lock_key: int = RESERVATION_SWEEP_LOCK_KEY,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._interval = interval
        self._lock_key = lock_key
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            _sweeper_loop(
                sessionmaker=self._sessionmaker,
                interval=self._interval,
                stop_event=self._stop,
                lock_key=self._lock_key,
            ),
            name="reservation-sweeper",
        )

    async def stop(self, *, timeout: float = 5.0) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except TimeoutError:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        finally:
            self._task = None
