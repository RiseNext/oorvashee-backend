"""ID generators."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def generate_order_number() -> str:
    """Human-readable order number.

    Format: `OOR-YYYYMM-XXXXXXXX` where XXXXXXXX is 8 random hex chars
    (32 bits of entropy). Collision probability is negligible at our
    scale — the UNIQUE constraint on `orders.order_number` catches the
    impossible case and the caller can retry.
    """
    month = datetime.now(UTC).strftime("%Y%m")
    suffix = secrets.token_hex(4).upper()
    return f"OOR-{month}-{suffix}"
