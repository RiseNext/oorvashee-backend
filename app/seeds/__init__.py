"""Catalog merchandising sync + seed system.

Public API:
- `MerchandisingSync` (`app.seeds.sync`) — production-safe, idempotent,
  non-destructive catalog reconciliation. The canonical entry point.
- `run_seeds` / `seed_status` (`app.seeds.runner`) — thin wrappers.

There is intentionally NO destructive reset: retirement is archive/deactivate,
never delete (preserves order history + referenced rows).

CLIs: `scripts/sync_catalog.py` (production-capable) and `scripts/seed_dev.py`
(dev convenience).
"""

from app.seeds.runner import run_seeds, seed_status
from app.seeds.sync import MerchandisingSync

__all__ = ["MerchandisingSync", "run_seeds", "seed_status"]
