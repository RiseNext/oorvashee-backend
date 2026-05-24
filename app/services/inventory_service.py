"""InventoryService — single owner of stock mutations.

Every code path that touches `inventory.stock` or `inventory.reserved` MUST
go through this service. Direct UPDATEs from anywhere else are a bug.

Concurrency rules (per INVENTORY_FLOW.md §9):
1. Locks are always taken on `inventory` rows, never on `product_variant`.
2. Multiple variants in one transaction are locked in a deterministic
   order (sorted by variant_id) to prevent classic two-phase deadlocks.
3. Stock pre-flight check uses `SELECT ... FOR UPDATE` so two concurrent
   checkouts can't both see the same "available" count.
4. Every mutation writes a `stock_movements` row (append-only audit).

Reservation model:
- Razorpay path: reserve on order creation; capture flips reservation
  into a real decrement; failed payment releases the reservation.
- COD path: decrement stock directly on order creation (no reservation).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.exceptions import OutOfStockError
from app.core.logging import get_logger
from app.models.enums import StockMovementReason
from app.models.inventory import Inventory, StockMovement
from app.services.base import BaseService

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StockRequirement:
    """One line-item's claim on stock."""

    variant_id: uuid.UUID
    quantity: int


class InventoryService(BaseService):
    # ------------------------------------------------------------------
    # Internal — lock + validate
    # ------------------------------------------------------------------

    async def _lock_inventory_rows(
        self, variant_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Inventory]:
        """Acquire row-level locks on the given variants' inventory rows.

        Deterministic ordering (sorted ids) avoids deadlocks when two
        concurrent checkouts touch the same variants in different orders.
        """
        if not variant_ids:
            return {}
        ordered = sorted(set(variant_ids))
        stmt = (
            select(Inventory)
            .where(Inventory.variant_id.in_(ordered))
            .order_by(Inventory.variant_id)
            .with_for_update()
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.variant_id: row for row in rows}

    @staticmethod
    def _ensure_available(inv: Inventory, requested: int, variant_id: uuid.UUID) -> None:
        available = inv.stock - inv.reserved
        if requested > available:
            raise OutOfStockError(
                f"Insufficient stock for variant {variant_id}",
                extra={
                    "variant_id": str(variant_id),
                    "available": available,
                    "requested": requested,
                },
            )

    # ------------------------------------------------------------------
    # Reservation (Razorpay path)
    # ------------------------------------------------------------------

    async def reserve(
        self,
        requirements: list[StockRequirement],
        *,
        order_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
    ) -> None:
        """Increment `reserved` for each variant. Locks rows first.

        Caller must already be inside a transaction.
        """
        locks = await self._lock_inventory_rows([r.variant_id for r in requirements])
        for req in requirements:
            inv = locks.get(req.variant_id)
            if inv is None:
                raise OutOfStockError(
                    "Variant has no inventory record",
                    extra={"variant_id": str(req.variant_id)},
                )
            self._ensure_available(inv, req.quantity, req.variant_id)
            inv.reserved += req.quantity
            self.session.add(
                StockMovement(
                    variant_id=req.variant_id,
                    delta=0,  # reservation isn't a stock change
                    reason=StockMovementReason.ORDER_PLACED,
                    order_id=order_id,
                    actor_user_id=actor_user_id,
                    note=f"reserved {req.quantity}",
                )
            )
        await self.session.flush()
        log.info(
            "inventory_reserved",
            order_id=str(order_id),
            lines=len(requirements),
            total_qty=sum(r.quantity for r in requirements),
        )

    async def release_reservation(
        self,
        requirements: list[StockRequirement],
        *,
        order_id: uuid.UUID,
        reason: StockMovementReason,
    ) -> None:
        """Decrement `reserved` without touching `stock`. Used on payment.failed
        / order cancellation when the reservation was never consumed.
        """
        locks = await self._lock_inventory_rows([r.variant_id for r in requirements])
        for req in requirements:
            inv = locks.get(req.variant_id)
            if inv is None:
                continue  # nothing to release
            inv.reserved = max(0, inv.reserved - req.quantity)
            self.session.add(
                StockMovement(
                    variant_id=req.variant_id,
                    delta=0,
                    reason=reason,
                    order_id=order_id,
                    note=f"reservation released ({req.quantity})",
                )
            )
        await self.session.flush()
        log.info(
            "inventory_reservation_released",
            order_id=str(order_id),
            lines=len(requirements),
            reason=reason.value,
        )

    async def consume_reservation(
        self,
        requirements: list[StockRequirement],
        *,
        order_id: uuid.UUID,
    ) -> None:
        """Reservation → real decrement. Called on payment.captured.

        Both stock and reserved drop by `quantity`; the CHECK constraint
        `reserved <= stock` is preserved.
        """
        locks = await self._lock_inventory_rows([r.variant_id for r in requirements])
        for req in requirements:
            inv = locks.get(req.variant_id)
            if inv is None:
                raise OutOfStockError(
                    "Variant has no inventory record at capture time",
                    extra={"variant_id": str(req.variant_id)},
                )
            # Reservation may have leaked (manual admin adjust) — clamp.
            consumed = min(req.quantity, inv.reserved)
            inv.reserved -= consumed
            inv.stock -= consumed
            # If reservation was short, the order is over-promised; emit a
            # warning movement so it's visible in the audit.
            short = req.quantity - consumed
            if short > 0:
                inv.stock -= short  # take the rest directly; CHECK guards <0
                log.warning(
                    "reservation_short",
                    order_id=str(order_id),
                    variant_id=str(req.variant_id),
                    short=short,
                )
            self.session.add(
                StockMovement(
                    variant_id=req.variant_id,
                    delta=-req.quantity,
                    reason=StockMovementReason.ORDER_PLACED,
                    order_id=order_id,
                    note="reservation consumed on payment capture",
                )
            )
        await self.session.flush()
        log.info("inventory_reservation_consumed", order_id=str(order_id))

    # ------------------------------------------------------------------
    # Direct decrement (COD path)
    # ------------------------------------------------------------------

    async def decrement(
        self,
        requirements: list[StockRequirement],
        *,
        order_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        reason: StockMovementReason = StockMovementReason.ORDER_PLACED,
    ) -> None:
        """Lock rows, validate, decrement stock, audit. Atomic per session txn."""
        locks = await self._lock_inventory_rows([r.variant_id for r in requirements])
        for req in requirements:
            inv = locks.get(req.variant_id)
            if inv is None:
                raise OutOfStockError(
                    "Variant has no inventory record",
                    extra={"variant_id": str(req.variant_id)},
                )
            self._ensure_available(inv, req.quantity, req.variant_id)
            inv.stock -= req.quantity
            self.session.add(
                StockMovement(
                    variant_id=req.variant_id,
                    delta=-req.quantity,
                    reason=reason,
                    order_id=order_id,
                    actor_user_id=actor_user_id,
                )
            )
        await self.session.flush()
        log.info(
            "inventory_decremented",
            order_id=str(order_id),
            lines=len(requirements),
            total_qty=sum(r.quantity for r in requirements),
        )

    async def restock(
        self,
        requirements: list[StockRequirement],
        *,
        order_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        reason: StockMovementReason = StockMovementReason.ORDER_CANCELLED,
    ) -> None:
        """Reverse a prior decrement (cancellation / refund / RTO)."""
        locks = await self._lock_inventory_rows([r.variant_id for r in requirements])
        for req in requirements:
            inv = locks.get(req.variant_id)
            if inv is None:
                continue
            inv.stock += req.quantity
            self.session.add(
                StockMovement(
                    variant_id=req.variant_id,
                    delta=req.quantity,
                    reason=reason,
                    order_id=order_id,
                    actor_user_id=actor_user_id,
                )
            )
        await self.session.flush()
        log.info(
            "inventory_restocked",
            order_id=str(order_id),
            lines=len(requirements),
            reason=reason.value,
        )
