"""CheckoutSessionService — atomic, cart-level stock reservation.

This is the heart of the redesign. When an authenticated user reaches the
checkout page, POST /checkout calls `start_checkout`, which:

  BEGIN (savepoint)
    1. Load the user's server cart (authoritative — never trust client items).
    2. Find-or-create the user's ONE live checkout_session (ACTIVE or
       PAYMENT_PROCESSING), locking it FOR UPDATE to serialise same-user calls.
    3. SELECT ... FOR UPDATE every variant's inventory row, in sorted id order
       (deterministic -> deadlock-free). THIS lock is the serialisation point
       that defeats the millisecond oversell race: a second checkout for the
       same variant blocks here until the first commits, then re-reads the
       (now larger) derived reserved sum.
    4. derived reserved = SUM(active reservations for the variant, excluding
       this session's own rows). available = inventory.stock - derived reserved.
    5. If EVERY line has available >= requested AND is buyable -> upsert one
       reservation per line (RESERVED, expires_at = now()+TTL). Idempotent on
       (checkout_session_id, variant_id) so a checkout reload refreshes instead
       of stacking. Prune reservations for variants no longer in the cart.
    6. If ANY line fails -> raise ReservationConflictError -> the whole
       savepoint rolls back. No partial holds. The error carries the per-line
       breakdown so the frontend shows exactly which item is unavailable.
  COMMIT (by get_db / session_scope).

Availability uses Model A: available = stock - active_reservations. The stored
`inventory.reserved` column and `inventory.sold_quantity` are NOT consulted.
Expired reservations are excluded lazily (expires_at > now()), so there is zero
timing gap between expiry-cron sweeps.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import ReservationConflictError, ValidationError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models.checkout_session import CheckoutSession
from app.models.enums import CheckoutSessionStatus, ProductStatus, ReservationStatus
from app.models.reservation import Reservation
from app.repositories.cart_repo import CartRepository
from app.repositories.reservation_repo import ReservationRepository
from app.schemas.checkout import CheckoutLineResult, CheckoutSessionRead
from app.services.base import BaseService
from app.services.inventory_service import InventoryService

log = get_logger(__name__)


class _Line:
    """A validated cart line ready for reservation."""

    __slots__ = ("variant_id", "quantity", "product_name", "variant_label", "buyable")

    def __init__(
        self,
        *,
        variant_id: uuid.UUID,
        quantity: int,
        product_name: str,
        variant_label: str | None,
        buyable: bool,
    ) -> None:
        self.variant_id = variant_id
        self.quantity = quantity
        self.product_name = product_name
        self.variant_label = variant_label
        self.buyable = buyable  # variant active + product published


class CheckoutSessionService(BaseService):
    @property
    def carts(self) -> CartRepository:
        return CartRepository(self.session)

    @property
    def reservations(self) -> ReservationRepository:
        return ReservationRepository(self.session)

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    # ------------------------------------------------------------------
    # POST /checkout — atomic cart-level reservation
    # ------------------------------------------------------------------

    async def start_checkout(self, *, user_id: uuid.UUID) -> CheckoutSessionRead:
        lines = await self._load_cart_lines(user_id)
        variant_ids = [line.variant_id for line in lines]
        ttl = get_settings().reservation_ttl_seconds
        expires_at = utcnow() + timedelta(seconds=ttl)

        async with self.session.begin_nested():
            session_obj, reusing = await self._acquire_session(user_id, expires_at)

            # A payment is already in flight for this cart — do not re-reserve
            # (that would desync the Razorpay order). Return the existing hold.
            if session_obj.status is CheckoutSessionStatus.PAYMENT_PROCESSING:
                return await self._read_existing(session_obj)

            # Serialisation point: lock every variant's inventory row.
            locks = await self.inventory.lock_inventory_rows(variant_ids)
            reserved_map = await self.reservations.active_reserved_for_variants(
                variant_ids, exclude_session_id=session_obj.id
            )

            results: list[CheckoutLineResult] = []
            all_ok = True
            for line in lines:
                inv = locks.get(line.variant_id)
                stock = inv.stock if inv is not None else 0
                reserved = reserved_map.get(line.variant_id, 0)
                available = max(stock - reserved, 0)
                ok = line.buyable and available >= line.quantity
                if not ok:
                    all_ok = False
                results.append(
                    CheckoutLineResult(
                        variant_id=line.variant_id,
                        product_name=line.product_name,
                        variant_label=line.variant_label,
                        requested=line.quantity,
                        available=available,
                        reserved=ok,
                        reason=None
                        if ok
                        else ("unavailable" if not line.buyable else "out_of_stock"),
                    )
                )

            if not all_ok:
                # Roll the whole savepoint back — no partial holds.
                raise ReservationConflictError(
                    "One or more items are no longer available",
                    extra={"lines": [r.model_dump(mode="json") for r in results]},
                )

            # All available — (re)hold every line atomically under the lock.
            session_obj.status = CheckoutSessionStatus.ACTIVE
            session_obj.expires_at = expires_at
            await self._upsert_reservations(
                session_id=session_obj.id,
                user_id=user_id,
                lines=lines,
                expires_at=expires_at,
            )
            await self._prune_stale(session_obj.id, variant_ids)
            await self.session.flush()

        log.info(
            "checkout_reserved",
            user_id=str(user_id),
            session_id=str(session_obj.id),
            reused=reusing,
            lines=len(lines),
        )
        return CheckoutSessionRead(
            session_id=session_obj.id,
            status=session_obj.status,
            expires_at=session_obj.expires_at,
            expires_in_seconds=ttl,
            lines=results,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_cart_lines(self, user_id: uuid.UUID) -> list[_Line]:
        cart = await self.carts.get_for_user(user_id)
        items = (
            await self.carts.list_items_hydrated(cart.id) if cart is not None else []
        )
        if not items:
            raise ValidationError("Your cart is empty", code="cart_empty")

        lines: list[_Line] = []
        for item in items:
            variant = item.variant
            if variant is None:
                continue
            product = variant.product
            label_parts = [p for p in (variant.color, variant.fabric) if p]
            buyable = (
                variant.is_active
                and product is not None
                and product.status is ProductStatus.PUBLISHED
            )
            lines.append(
                _Line(
                    variant_id=variant.id,
                    quantity=item.quantity,
                    product_name=product.name if product else "Item",
                    variant_label=" · ".join(label_parts) if label_parts else None,
                    buyable=buyable,
                )
            )
        if not lines:
            raise ValidationError("Your cart is empty", code="cart_empty")
        return lines

    async def _acquire_session(
        self, user_id: uuid.UUID, expires_at
    ) -> tuple[CheckoutSession, bool]:
        """Find-or-create the user's single live checkout session.

        Reuses an existing ACTIVE/PAYMENT_PROCESSING session (locked FOR UPDATE
        to serialise same-user concurrency). On a create race, the partial
        unique index rejects the loser; we absorb the IntegrityError and reuse
        the winner.
        """
        existing = await self.reservations.get_active_session_for_user(
            user_id, for_update=True
        )
        if existing is not None:
            return existing, True

        new_session = CheckoutSession(
            user_id=user_id,
            status=CheckoutSessionStatus.ACTIVE,
            expires_at=expires_at,
        )
        self.session.add(new_session)
        try:
            async with self.session.begin_nested():
                await self.session.flush()
            return new_session, False
        except IntegrityError:
            # Lost the create race to a concurrent same-user request.
            winner = await self.reservations.get_active_session_for_user(
                user_id, for_update=True
            )
            if winner is None:
                raise
            return winner, True

    async def _upsert_reservations(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        lines: list[_Line],
        expires_at,
    ) -> None:
        """Insert/refresh one reservation per line. Idempotent on
        (checkout_session_id, variant_id) so a checkout reload refreshes the
        hold rather than stacking duplicates."""
        for line in lines:
            stmt = (
                pg_insert(Reservation)
                .values(
                    checkout_session_id=session_id,
                    variant_id=line.variant_id,
                    user_id=user_id,
                    quantity=line.quantity,
                    status=ReservationStatus.RESERVED,
                    expires_at=expires_at,
                )
                .on_conflict_do_update(
                    index_elements=["checkout_session_id", "variant_id"],
                    set_={
                        "quantity": line.quantity,
                        "status": ReservationStatus.RESERVED,
                        "expires_at": expires_at,
                        "updated_at": func.now(),
                    },
                )
            )
            await self.session.execute(stmt)

    async def _prune_stale(
        self, session_id: uuid.UUID, keep_variant_ids: list[uuid.UUID]
    ) -> None:
        """Drop reservations for variants the user removed from the cart since
        the session was created (reuse path)."""
        stmt = delete(Reservation).where(
            Reservation.checkout_session_id == session_id,
            Reservation.variant_id.notin_(keep_variant_ids),
        )
        await self.session.execute(stmt)

    async def _read_existing(self, session_obj: CheckoutSession) -> CheckoutSessionRead:
        """Return an in-flight (PAYMENT_PROCESSING) session as-is, without
        re-reserving — payment is already underway for this cart."""
        rows = await self.reservations.list_for_session(session_obj.id)
        now = utcnow()
        remaining = int((session_obj.expires_at - now).total_seconds())
        return CheckoutSessionRead(
            session_id=session_obj.id,
            status=session_obj.status,
            expires_at=session_obj.expires_at,
            expires_in_seconds=max(remaining, 0),
            lines=[
                CheckoutLineResult(
                    variant_id=r.variant_id,
                    product_name="",
                    variant_label=None,
                    requested=r.quantity,
                    available=r.quantity,
                    reserved=True,
                    reason=None,
                )
                for r in rows
            ],
        )
