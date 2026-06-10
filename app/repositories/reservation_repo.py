"""Reservation + CheckoutSession data access.

The derived "reserved" quantity for a variant is the single source of truth for
how much stock is currently held — it replaces the deprecated stored
`inventory.reserved` column. All reads here exclude expired rows lazily
(`expires_at > now()`) so there is no timing gap between cron sweeps.

No business rules / no commits — services own those.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from app.models.checkout_session import CheckoutSession
from app.models.enums import CheckoutSessionStatus, ReservationStatus
from app.models.product_variant import ProductVariant
from app.models.reservation import Reservation
from app.repositories.base import BaseRepository

# Reservation states that actively hold stock (count toward derived reserved).
ACTIVE_RESERVATION_STATUSES = (
    ReservationStatus.RESERVED,
    ReservationStatus.PAYMENT_PROCESSING,
)
ACTIVE_SESSION_STATUSES = (
    CheckoutSessionStatus.ACTIVE,
    CheckoutSessionStatus.PAYMENT_PROCESSING,
)


class ReservationRepository(BaseRepository[Reservation]):
    model = Reservation

    async def active_reserved_for_variants(
        self,
        variant_ids: Sequence[uuid.UUID],
        *,
        exclude_session_id: uuid.UUID | None = None,
    ) -> dict[uuid.UUID, int]:
        """SUM(quantity) of active, non-expired reservations per variant.

        `exclude_session_id` omits a given session's own rows — used so that
        re-reserving for an existing session doesn't count the user's own
        hold against availability (idempotent reload).

        Returns a dict; variants with no active reservations are absent (treat
        as 0). Backed by ix_reservations_variant_status_expires.
        """
        if not variant_ids:
            return {}
        stmt = (
            select(
                Reservation.variant_id,
                func.coalesce(func.sum(Reservation.quantity), 0),
            )
            .where(
                Reservation.variant_id.in_(variant_ids),
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                Reservation.expires_at > func.now(),
            )
            .group_by(Reservation.variant_id)
        )
        if exclude_session_id is not None:
            stmt = stmt.where(
                Reservation.checkout_session_id != exclude_session_id
            )
        rows = (await self.session.execute(stmt)).all()
        return {variant_id: int(total) for variant_id, total in rows}

    async def get_active_session_for_user(
        self,
        user_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> CheckoutSession | None:
        """The user's one live checkout session (ACTIVE or PAYMENT_PROCESSING).

        At most one exists (enforced by the partial unique index
        uq_checkout_sessions_one_active_per_user). `for_update=True` locks the
        row to serialise concurrent checkouts from the same user.
        """
        stmt = select(CheckoutSession).where(
            CheckoutSession.user_id == user_id,
            CheckoutSession.status.in_(ACTIVE_SESSION_STATUSES),
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_session(
        self, session_id: uuid.UUID
    ) -> CheckoutSession | None:
        return await self.session.get(CheckoutSession, session_id)

    async def get_session_for_update(
        self, session_id: uuid.UUID
    ) -> CheckoutSession | None:
        """Lock a session row (serialises pay vs. concurrent reload/cancel)."""
        stmt = (
            select(CheckoutSession)
            .where(CheckoutSession.id == session_id)
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_reserved_for_session(
        self, session_id: uuid.UUID
    ) -> Sequence[Reservation]:
        """The still-RESERVED lines of a session (the ones a pay will convert)."""
        stmt = (
            select(Reservation)
            .where(
                Reservation.checkout_session_id == session_id,
                Reservation.status == ReservationStatus.RESERVED,
            )
            .order_by(Reservation.variant_id)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_for_session(
        self, session_id: uuid.UUID
    ) -> Sequence[Reservation]:
        stmt = (
            select(Reservation)
            .where(Reservation.checkout_session_id == session_id)
            .order_by(Reservation.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    # ---------- Admin views -------------------------------------------------

    async def total_active_reserved(self) -> int:
        """SUM(quantity) of all active, non-expired reservations on active
        variants — the derived `total_reserved_units` for the health tile."""
        stmt = (
            select(func.coalesce(func.sum(Reservation.quantity), 0))
            .select_from(Reservation)
            .join(ProductVariant, ProductVariant.id == Reservation.variant_id)
            .where(
                ProductVariant.is_active.is_(True),
                Reservation.status.in_(ACTIVE_RESERVATION_STATUSES),
                Reservation.expires_at > func.now(),
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def status_counts_for_variant(
        self, variant_id: uuid.UUID
    ) -> dict[ReservationStatus, int]:
        """Count of reservations per status for one variant (drives the
        Active/Expired/Completed/Cancelled breakdown on the detail view)."""
        stmt = (
            select(Reservation.status, func.count())
            .where(Reservation.variant_id == variant_id)
            .group_by(Reservation.status)
        )
        rows = (await self.session.execute(stmt)).all()
        return {status: int(count) for status, count in rows}

    def admin_list_query(
        self,
        *,
        variant_id: uuid.UUID | None = None,
        statuses: list[ReservationStatus] | None = None,
        session_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Select:
        """Paginated admin reservation list, hydrated with variant + product.
        Newest first. Filters compose."""
        stmt = select(Reservation).options(
            selectinload(Reservation.variant).selectinload(ProductVariant.product)
        )
        if variant_id is not None:
            stmt = stmt.where(Reservation.variant_id == variant_id)
        if statuses:
            stmt = stmt.where(Reservation.status.in_(statuses))
        if session_id is not None:
            stmt = stmt.where(Reservation.checkout_session_id == session_id)
        if user_id is not None:
            stmt = stmt.where(Reservation.user_id == user_id)
        return stmt.order_by(
            Reservation.created_at.desc(), Reservation.id.desc()
        )
