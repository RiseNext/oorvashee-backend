"""Shared seed utilities.

Idempotency strategy:
- Every seeded entity has a natural key (slug, SKU, name, or title+placement).
- `get_or_create` does SELECT → INSERT-if-missing. Safe for a single-process
  seeder; we don't try to handle concurrent runners.
- Mutable fields (price, description, stock, tags, etc.) are refreshed on
  every run via the `update_fields` arg — so editing the seed data and
  re-running picks up the change without a reset.

Reporting:
- Each loader returns a `SeedReport` with per-entity counts.
- The runner aggregates them for the CLI to print.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


@dataclass
class SeedReport:
    """Per-entity counts for a single seed run."""

    created: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    updated: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def merge(self, other: SeedReport) -> None:
        for k, v in other.created.items():
            self.created[k] += v
        for k, v in other.updated.items():
            self.updated[k] += v
        for k, v in other.skipped.items():
            self.skipped[k] += v

    def lines(self) -> list[str]:
        keys = sorted(set(self.created) | set(self.updated) | set(self.skipped))
        return [
            f"  {k:24s}  created={self.created[k]:>3}  "
            f"updated={self.updated[k]:>3}  skipped={self.skipped[k]:>3}"
            for k in keys
        ]


async def get_or_create(
    session: AsyncSession,
    model: type[ModelT],
    *,
    lookup: dict[str, Any],
    defaults: dict[str, Any] | None = None,
    update_fields: dict[str, Any] | None = None,
) -> tuple[ModelT, bool, bool]:
    """SELECT by `lookup`; INSERT with `lookup + defaults` if missing.

    Returns `(instance, created, updated)`.

    `update_fields` (if provided) refreshes those columns on an existing row,
    so re-running the seed after editing the data picks up the change without
    needing a `reset`.
    """
    stmt = select(model).filter_by(**lookup)
    instance = (await session.execute(stmt)).scalar_one_or_none()

    if instance is None:
        attrs = {**lookup, **(defaults or {}), **(update_fields or {})}
        instance = model(**attrs)
        session.add(instance)
        await session.flush()
        return instance, True, False

    if update_fields:
        changed = False
        for k, v in update_fields.items():
            if getattr(instance, k) != v:
                setattr(instance, k, v)
                changed = True
        if changed:
            await session.flush()
            return instance, False, True

    return instance, False, False


def picsum_url(seed: str, *, width: int = 800, height: int = 1000) -> str:
    """Deterministic placeholder image URL — same seed → same image.

    Picsum.photos is a long-running public image service; safe for dev seeds.
    Replace with real Cloudinary URLs once the admin upload flow lands.
    """
    return f"https://picsum.photos/seed/{seed}/{width}/{height}"


def seed_public_id(slug: str, index: int) -> str:
    """Stable placeholder Cloudinary `public_id` for seed images."""
    return f"seeds/products/{slug}/{index:02d}"
