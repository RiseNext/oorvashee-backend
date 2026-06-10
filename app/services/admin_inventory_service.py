"""AdminInventoryService — admin-facing stock operations.

Composes `InventoryService.lock_inventory_rows` for concurrency safety:
the SAME row-lock that protects checkout from overselling protects admin
adjustments from racing with a paying customer's reservation.

Operations:
- `adjust(...)` — increment / decrement / set absolute stock. Row-locked,
  reservation-aware (never lets stock fall below reserved), writes a
  `stock_movements` row + an `audit_logs` row, recomputes parent product
  availability (PUBLISHED ↔ UNAVAILABLE auto-flip).
- `set_low_stock_threshold(...)` — administrative metadata only; no
  row lock needed.
- `list_inventory` / `get_variant_detail` / `list_movements` / `health` —
  read-only.

Reason whitelist enforced server-side (defense in depth on top of the
schema's restriction).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.pagination import OffsetParams, Page, paginate_offset
from app.db.base import utcnow
from app.models.enums import AuditAction, ReservationStatus, StockMovementReason
from app.models.inventory import Inventory, StockMovement
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.reservation import Reservation
from app.repositories.inventory_repo import InventoryRepository
from app.repositories.reservation_repo import ReservationRepository
from app.schemas.admin_inventory import (
    AdjustmentMode,
    InventoryDetail,
    InventoryHealthSummary,
    InventoryListItem,
    MovementItem,
    ReservationAdminItem,
    ReservationCounts,
    StockAdjustmentRequest,
    StockAdjustmentResult,
    ThresholdUpdateRequest,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService
from app.services.inventory_service import InventoryService

log = get_logger(__name__)


# Admins may pick only these reasons. System reasons (order_placed,
# order_cancelled, csv_import) flow through their owning services.
_ADMIN_PICKABLE_REASONS: frozenset[StockMovementReason] = frozenset(
    {
        StockMovementReason.MANUAL_ADJUSTMENT,
        StockMovementReason.RESTOCK,
    }
)


class AdminInventoryService(BaseService):
    @property
    def repo(self) -> InventoryRepository:
        return InventoryRepository(self.session)

    @property
    def reservations(self) -> ReservationRepository:
        return ReservationRepository(self.session)

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ======================================================================
    # Adjust
    # ======================================================================

    async def adjust(
        self,
        variant_id: uuid.UUID,
        body: StockAdjustmentRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> StockAdjustmentResult:
        """Apply a manual stock adjustment under a row-level lock.

        Validation rules:
          - `reason` must be admin-pickable (whitelist above).
          - For INCREMENT/DECREMENT, `value > 0`. SET allows 0.
          - Resulting stock must be `>= 0` AND `>= reserved` (preserving
            the invariant that reservations are always backed by stock).

        Concurrency:
          - Acquires the same `SELECT ... FOR UPDATE` lock that checkout
            uses; an in-flight reservation can't race with an admin
            "set to 2" call.
        """
        if body.reason not in _ADMIN_PICKABLE_REASONS:
            raise ValidationError(
                f"Reason '{body.reason.value}' is not admin-pickable",
                code="reason_not_allowed",
                extra={"allowed": sorted(r.value for r in _ADMIN_PICKABLE_REASONS)},
            )

        variant = await self._require_variant(variant_id)

        locks = await self.inventory.lock_inventory_rows([variant_id])
        inv = locks.get(variant_id)
        if inv is None:
            # No inventory row — defensive. AdminProductService seeds one
            # alongside every variant; this would indicate a data anomaly.
            raise NotFoundError("Inventory record not found for variant")

        stock_before = inv.stock
        target = self._compute_target_stock(body, stock_before)

        # Derived reserved (active reservations) computed under the SAME lock,
        # so an in-flight reservation can't race an admin "set to N". The
        # deprecated stored inventory.reserved is a legacy floor the DB CHECK
        # still enforces — guard against whichever is higher to give a clean
        # 409 instead of an IntegrityError.
        derived_reserved = (
            await self.reservations.active_reserved_for_variants([variant_id])
        ).get(variant_id, 0)
        reserved_floor = max(inv.reserved, derived_reserved)

        if target < 0:
            raise ConflictError(
                "Adjustment would push stock below zero",
                code="adjustment_negative_stock",
                extra={"current_stock": stock_before, "target_stock": target},
            )
        if target < reserved_floor:
            raise ConflictError(
                "Adjustment would push stock below reserved units; "
                "release reservations first or cancel pending orders",
                code="adjustment_below_reserved",
                extra={
                    "current_stock": stock_before,
                    "target_stock": target,
                    "reserved": reserved_floor,
                },
            )

        delta = target - stock_before
        if delta == 0:
            # No-op — surface as a 200 with the current state. Idempotent
            # under accidental double-submit.
            log.info(
                "inventory_adjust_noop",
                variant_id=str(variant_id),
                stock=stock_before,
                actor=str(actor_user_id),
            )
            # Still emit an audit log (admin intent was recorded), but no
            # stock_movement row — that table is for actual deltas.
            await self.audit.record(
                action=AuditAction.STOCK_ADJUSTED,
                entity_type="inventory",
                entity_id=variant_id,
                actor_user_id=actor_user_id,
                summary=(
                    f"Admin no-op adjust on SKU {variant.sku} "
                    f"(mode={body.mode.value}, value={body.value})"
                ),
                metadata={
                    "mode": body.mode.value,
                    "requested_value": body.value,
                    "stock": stock_before,
                    "delta": 0,
                    "reason": body.reason.value,
                },
                request_id=request_id,
            )
            return StockAdjustmentResult(
                variant_id=variant_id,
                sku=variant.sku,
                stock_before=stock_before,
                stock_after=stock_before,
                reserved=derived_reserved,
                delta=0,
                movement_id=uuid.UUID(int=0),  # sentinel for no-op
                product_status_after=None,
            )

        inv.stock = target
        movement = StockMovement(
            variant_id=variant_id,
            delta=delta,
            reason=body.reason,
            actor_user_id=actor_user_id,
            note=body.note,
        )
        self.session.add(movement)
        await self.session.flush()

        # AuditLog is the cross-system trail; stock_movements is the
        # domain-specific audit. Both exist on purpose — auditors grep
        # audit_logs, ops grep stock_movements.
        await self.audit.record(
            action=AuditAction.STOCK_ADJUSTED,
            entity_type="inventory",
            entity_id=variant_id,
            actor_user_id=actor_user_id,
            summary=(
                f"SKU {variant.sku}: stock {stock_before} → {target} "
                f"(Δ {delta:+d}, reason={body.reason.value})"
            ),
            metadata={
                "mode": body.mode.value,
                "requested_value": body.value,
                "stock_before": stock_before,
                "stock_after": target,
                "delta": delta,
                "reserved": derived_reserved,
                "reason": body.reason.value,
                "movement_id": str(movement.id),
            },
            request_id=request_id,
        )

        # Auto-flip product status if we just exhausted or replenished
        # the parent. Runs in the same transaction so the page-load that
        # follows this response shows the right status.
        new_status = await self.inventory.recompute_product_availability(
            variant.product_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

        log.info(
            "inventory_adjusted",
            variant_id=str(variant_id),
            sku=variant.sku,
            stock_before=stock_before,
            stock_after=target,
            delta=delta,
            reason=body.reason.value,
            actor=str(actor_user_id),
            product_status_after=new_status.value if new_status else None,
        )

        return StockAdjustmentResult(
            variant_id=variant_id,
            sku=variant.sku,
            stock_before=stock_before,
            stock_after=target,
            reserved=derived_reserved,
            delta=delta,
            movement_id=movement.id,
            product_status_after=new_status.value if new_status else None,
        )

    # ======================================================================
    # Threshold
    # ======================================================================

    async def set_low_stock_threshold(
        self,
        variant_id: uuid.UUID,
        body: ThresholdUpdateRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> InventoryDetail:
        # No row lock needed — threshold is metadata, doesn't gate stock.
        variant = await self._require_variant(variant_id)
        inv = await self.repo.get_for_variant(variant_id)
        if inv is None:
            raise NotFoundError("Inventory record not found for variant")

        if inv.low_stock_threshold == body.low_stock_threshold:
            return await self._build_detail(variant, inv)

        before = inv.low_stock_threshold
        inv.low_stock_threshold = body.low_stock_threshold
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="inventory",
            entity_id=variant_id,
            actor_user_id=actor_user_id,
            summary=(
                f"SKU {variant.sku}: low_stock_threshold "
                f"{before} → {body.low_stock_threshold}"
            ),
            metadata={
                "low_stock_threshold": {
                    "from": before,
                    "to": body.low_stock_threshold,
                },
            },
            request_id=request_id,
        )
        return await self._build_detail(variant, inv)

    # ======================================================================
    # Reads
    # ======================================================================

    async def list_inventory(
        self,
        *,
        params: OffsetParams,
        q: str | None,
        low_stock_only: bool,
        out_of_stock_only: bool,
    ) -> Page[InventoryListItem]:
        if low_stock_only and out_of_stock_only:
            raise ValidationError(
                "Pass at most one of `low_stock_only` and `out_of_stock_only`",
                code="conflicting_filters",
            )
        stmt = self.repo.admin_list_query(
            q=q,
            low_stock_only=low_stock_only,
            out_of_stock_only=out_of_stock_only,
        )
        rows, total = await paginate_offset(self.session, stmt, params)
        reserved_map = await self.reservations.active_reserved_for_variants(
            [inv.variant_id for inv in rows]
        )
        items = [
            self._to_list_item(inv, reserved_map.get(inv.variant_id, 0))
            for inv in rows
        ]
        return Page[InventoryListItem](items=items, total=total)

    async def list_reservations(
        self,
        *,
        params: OffsetParams,
        variant_id: uuid.UUID | None,
        statuses: list[ReservationStatus] | None,
        session_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
    ) -> Page[ReservationAdminItem]:
        """Admin reservation lists (Active / Expired / Completed / Cancelled).
        Filter by `statuses` to render each tab."""
        stmt = self.reservations.admin_list_query(
            variant_id=variant_id,
            statuses=statuses,
            session_id=session_id,
            user_id=user_id,
        )
        rows, total = await paginate_offset(self.session, stmt, params)
        now = utcnow()
        items = [self._to_reservation_item(r, now) for r in rows]
        return Page[ReservationAdminItem](items=items, total=total)

    async def get_variant_detail(self, variant_id: uuid.UUID) -> InventoryDetail:
        inv = await self.repo.get_with_variant_and_product(variant_id)
        if inv is None:
            raise NotFoundError("Inventory record not found for variant")
        return await self._build_detail(inv.variant, inv)

    async def list_movements(
        self,
        *,
        params: OffsetParams,
        variant_id: uuid.UUID | None,
        reasons: list[StockMovementReason] | None,
        actor_user_id: uuid.UUID | None,
        order_id: uuid.UUID | None,
        since: datetime | None,
        until: datetime | None,
    ) -> Page[MovementItem]:
        stmt = self.repo.movements_query(
            variant_id=variant_id,
            reasons=reasons,
            actor_user_id=actor_user_id,
            order_id=order_id,
            since=since,
            until=until,
        )
        rows, total = await paginate_offset(self.session, stmt, params)
        items = [self._to_movement_item(m) for m in rows]
        return Page[MovementItem](items=items, total=total)

    async def health(self) -> InventoryHealthSummary:
        agg = await self.repo.health_aggregates()
        active = agg["active_variants"]
        out = agg["out_of_stock_variants"]
        low = agg["low_stock_variants"]
        total_stock = agg["total_stock_units"]
        # Reserved is DERIVED from active reservations (matches checkout).
        total_reserved = await self.reservations.total_active_reserved()
        return InventoryHealthSummary(
            active_variants=active,
            total_stock_units=total_stock,
            total_reserved_units=total_reserved,
            available_units=max(total_stock - total_reserved, 0),
            total_sold_units=agg["total_sold_units"],
            out_of_stock_variants=out,
            low_stock_variants=low,
            healthy_variants=max(active - out - low, 0),
        )

    # ======================================================================
    # Internal helpers
    # ======================================================================

    async def _require_variant(self, variant_id: uuid.UUID) -> ProductVariant:
        variant = await self.session.get(ProductVariant, variant_id)
        if variant is None:
            raise NotFoundError("Variant not found")
        return variant

    @staticmethod
    def _compute_target_stock(body: StockAdjustmentRequest, current: int) -> int:
        match body.mode:
            case AdjustmentMode.SET:
                return body.value
            case AdjustmentMode.INCREMENT:
                return current + body.value
            case AdjustmentMode.DECREMENT:
                return current - body.value

    @staticmethod
    def _to_list_item(inv: Inventory, reserved: int) -> InventoryListItem:
        variant: ProductVariant = inv.variant
        product: Product = variant.product
        price = variant.price_override or product.base_price
        label_parts = [p for p in (variant.color, variant.fabric, variant.size) if p]
        return InventoryListItem(
            variant_id=variant.id,
            sku=variant.sku,
            product_id=product.id,
            product_name=product.name,
            product_slug=product.slug,
            variant_label=" / ".join(label_parts) if label_parts else None,
            stock=inv.stock,
            reserved=reserved,
            available=max(inv.stock - reserved, 0),
            sold=inv.sold_quantity,
            low_stock_threshold=inv.low_stock_threshold,
            is_low_stock=(0 < inv.stock <= inv.low_stock_threshold),
            is_out_of_stock=(inv.stock == 0),
            is_default_variant=variant.is_default,
            price=price,
        )

    @staticmethod
    def _bucket(status: ReservationStatus) -> str:
        if status in (
            ReservationStatus.RESERVED,
            ReservationStatus.PAYMENT_PROCESSING,
        ):
            return "active"
        if status is ReservationStatus.EXPIRED:
            return "expired"
        if status is ReservationStatus.COMPLETED:
            return "completed"
        return "cancelled"

    @staticmethod
    def _reservation_counts(
        counts: dict[ReservationStatus, int],
    ) -> ReservationCounts:
        return ReservationCounts(
            active=(
                counts.get(ReservationStatus.RESERVED, 0)
                + counts.get(ReservationStatus.PAYMENT_PROCESSING, 0)
            ),
            expired=counts.get(ReservationStatus.EXPIRED, 0),
            completed=counts.get(ReservationStatus.COMPLETED, 0),
            cancelled=counts.get(ReservationStatus.CANCELLED, 0),
        )

    @staticmethod
    def _to_reservation_item(r: Reservation, now: datetime) -> ReservationAdminItem:
        variant: ProductVariant | None = getattr(r, "variant", None)
        sku = variant.sku if variant else ""
        product_name = variant.product.name if variant and variant.product else ""
        is_active = r.status in (
            ReservationStatus.RESERVED,
            ReservationStatus.PAYMENT_PROCESSING,
        )
        return ReservationAdminItem(
            id=r.id,
            checkout_session_id=r.checkout_session_id,
            variant_id=r.variant_id,
            sku=sku,
            product_name=product_name,
            user_id=r.user_id,
            quantity=r.quantity,
            status=r.status,
            bucket=AdminInventoryService._bucket(r.status),
            is_expired=is_active and r.expires_at <= now,
            expires_at=r.expires_at,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    async def _build_detail(
        self, variant: ProductVariant, inv: Inventory
    ) -> InventoryDetail:
        product = await self.session.get(Product, variant.product_id)
        if product is None:  # defensive — FK should guarantee this
            raise NotFoundError("Parent product not found")
        recent = await self.repo.recent_movements_for_variant(variant.id, limit=20)
        price: Decimal = variant.price_override or product.base_price
        reserved = (
            await self.reservations.active_reserved_for_variants([variant.id])
        ).get(variant.id, 0)
        counts = await self.reservations.status_counts_for_variant(variant.id)
        return InventoryDetail(
            variant_id=variant.id,
            sku=variant.sku,
            product_id=product.id,
            product_name=product.name,
            product_slug=product.slug,
            color=variant.color,
            fabric=variant.fabric,
            size=variant.size,
            stock=inv.stock,
            reserved=reserved,
            available=max(inv.stock - reserved, 0),
            sold=inv.sold_quantity,
            low_stock_threshold=inv.low_stock_threshold,
            is_low_stock=(0 < inv.stock <= inv.low_stock_threshold),
            is_out_of_stock=(inv.stock == 0),
            is_default_variant=variant.is_default,
            price=price,
            reservation_counts=self._reservation_counts(counts),
            recent_movements=[
                await self._movement_item_with_product(m) for m in recent
            ],
        )

    async def _movement_item_with_product(self, m: StockMovement) -> MovementItem:
        # Recent movements query is by-variant, so we already know the
        # variant + product context. Load product details once.
        variant = await self.session.get(ProductVariant, m.variant_id)
        product_name = ""
        sku = ""
        if variant is not None:
            sku = variant.sku
            product = await self.session.get(Product, variant.product_id)
            if product is not None:
                product_name = product.name
        return MovementItem(
            id=m.id,
            variant_id=m.variant_id,
            sku=sku,
            product_name=product_name,
            delta=m.delta,
            reason=m.reason,
            order_id=m.order_id,
            actor_user_id=m.actor_user_id,
            reference=m.reference,
            note=m.note,
            created_at=m.created_at,
        )

    @staticmethod
    def _to_movement_item(m: StockMovement) -> MovementItem:
        """Movements-list path: variant + product pre-loaded by repo query."""
        variant: ProductVariant | None = getattr(m, "variant", None)
        sku = variant.sku if variant else ""
        product_name = (
            variant.product.name if variant and variant.product else ""
        )
        return MovementItem(
            id=m.id,
            variant_id=m.variant_id,
            sku=sku,
            product_name=product_name,
            delta=m.delta,
            reason=m.reason,
            order_id=m.order_id,
            actor_user_id=m.actor_user_id,
            reference=m.reference,
            note=m.note,
            created_at=m.created_at,
        )

    @staticmethod
    def list_admin_pickable_reasons() -> Sequence[StockMovementReason]:
        return sorted(_ADMIN_PICKABLE_REASONS, key=lambda r: r.value)
