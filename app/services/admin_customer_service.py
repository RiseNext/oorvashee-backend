"""AdminCustomerService — CRM-style views over the local `users` table.

Read-mostly. The only mutation here is "add admin note" (writes
`audit_logs` with `metadata.type=admin_note`). Real customer mutations
(create/update/delete) flow through the Clerk webhook in
`UserSyncService` — admin never edits users locally.

KPI math (AOV, days-since-last-order, repeat detection) lives here so
the response shape is consistent regardless of where the underlying
counts come from.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.pagination import OffsetParams, Page
from app.models.address import Address
from app.models.audit_log import AuditLog
from app.models.enums import AuditAction, OrderStatus, PaymentStatus
from app.models.order import Order
from app.models.role import Role, UserRole
from app.models.user import User
from app.models.user_profile import UserProfile
from app.repositories.customer_repo import CustomerRepository
from app.schemas.admin_customer import (
    AddCustomerNoteRequest,
    AdminCustomerDetail,
    AdminCustomerListItem,
    CustomerActivityEvent,
    CustomerActivityEventType,
    CustomerAddressRead,
    CustomerKPI,
    CustomerOrderSummary,
    CustomerProfileRead,
    CustomerRoleRead,
    CustomerSegmentSummary,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService

log = get_logger(__name__)

DELETED_EMAIL_PREFIX = "deleted+"


class AdminCustomerService(BaseService):
    @property
    def repo(self) -> CustomerRepository:
        return CustomerRepository(self.session)

    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ======================================================================
    # List
    # ======================================================================

    async def list_customers(
        self,
        *,
        params: OffsetParams,
        q: str | None,
        has_orders: bool | None,
        sort: str,
        include_deleted: bool,
    ) -> Page[AdminCustomerListItem]:
        stmt = self.repo.admin_list_query(
            q=q,
            include_deleted=include_deleted,
            has_orders=has_orders,
            sort=sort,
        )
        rows, total = await self.repo.paginate_admin_list(
            stmt, limit=params.page_size, offset=params.offset
        )
        items = [self._row_to_list_item(row) for row in rows]
        return Page[AdminCustomerListItem](items=items, total=total)

    # ======================================================================
    # Detail
    # ======================================================================

    async def get_customer_detail(self, user_id: uuid.UUID) -> AdminCustomerDetail:
        user = await self.repo.get_admin(user_id)
        if user is None:
            raise NotFoundError("Customer not found")

        kpi_raw = await self.repo.user_kpi(user_id)
        recent = await self.repo.recent_orders(user_id, limit=5)
        return self._to_detail(user, kpi_raw, recent)

    # ======================================================================
    # Order history (paginated)
    # ======================================================================

    async def list_customer_orders(
        self,
        user_id: uuid.UUID,
        *,
        params: OffsetParams,
    ) -> Page[CustomerOrderSummary]:
        # Confirm customer exists so a typo gets a clean 404 instead of an
        # empty page (which would mask the bug).
        user = await self.repo.get_admin(user_id)
        if user is None:
            raise NotFoundError("Customer not found")

        rows, total = await self.repo.list_orders_for_user(
            user_id, limit=params.page_size, offset=params.offset
        )
        items = [self._order_to_summary(o) for o in rows]
        return Page[CustomerOrderSummary](items=items, total=total)

    # ======================================================================
    # Activity timeline
    # ======================================================================

    async def get_activity(
        self, user_id: uuid.UUID
    ) -> list[CustomerActivityEvent]:
        user = await self.repo.get_admin(user_id)
        if user is None:
            raise NotFoundError("Customer not found")

        events: list[CustomerActivityEvent] = []

        # Sign-up event — the canonical acquisition timestamp.
        events.append(
            CustomerActivityEvent(
                at=user.created_at,
                type=CustomerActivityEventType.SIGNED_UP,
                actor_user_id=None,
                summary="Account created via Clerk",
                metadata={"clerk_user_id": user.clerk_user_id},
            )
        )

        # Orders — placed + paid + cancelled.
        for order in await self.repo.order_events(user_id):
            placed_at = order.placed_at or order.created_at
            events.append(
                CustomerActivityEvent(
                    at=placed_at,
                    type=CustomerActivityEventType.ORDER_PLACED,
                    actor_user_id=None,
                    summary=f"Placed order {order.order_number}",
                    metadata={
                        "order_id": str(order.id),
                        "order_number": order.order_number,
                        "total": str(order.total),
                        "payment_method": order.payment_method.value,
                    },
                )
            )
            if order.payment_status is PaymentStatus.PAID and order.placed_at:
                events.append(
                    CustomerActivityEvent(
                        at=order.placed_at,
                        type=CustomerActivityEventType.ORDER_PAID,
                        actor_user_id=None,
                        summary=f"Paid order {order.order_number}",
                        metadata={
                            "order_id": str(order.id),
                            "order_number": order.order_number,
                            "total": str(order.total),
                        },
                    )
                )
            if order.status is OrderStatus.CANCELLED and order.cancelled_at:
                events.append(
                    CustomerActivityEvent(
                        at=order.cancelled_at,
                        type=CustomerActivityEventType.ORDER_CANCELLED,
                        actor_user_id=None,
                        summary=f"Order {order.order_number} cancelled",
                        metadata={"order_id": str(order.id)},
                    )
                )

        # Roles assigned.
        for ur, role in await self.repo.assigned_role_events(user_id):
            events.append(
                CustomerActivityEvent(
                    at=ur.created_at,
                    type=CustomerActivityEventType.ROLE_ASSIGNED,
                    actor_user_id=ur.assigned_by,
                    summary=f"Role '{role.name}' assigned",
                    metadata={"role": role.name, "is_system": role.is_system},
                )
            )

        # Admin notes.
        for row in await self.repo.audit_events_for_user(user_id):
            event = self._audit_to_event(row)
            if event is not None:
                events.append(event)

        events.sort(key=lambda e: e.at, reverse=True)
        return events

    # ======================================================================
    # Add admin note
    # ======================================================================

    async def add_note(
        self,
        user_id: uuid.UUID,
        body: AddCustomerNoteRequest,
        *,
        actor_user_id: uuid.UUID,
        request_id: str | None = None,
    ) -> CustomerActivityEvent:
        user = await self.repo.get_admin(user_id)
        if user is None:
            raise NotFoundError("Customer not found")
        if user.deleted_at is not None:
            raise ValidationError(
                "Cannot annotate a deleted customer",
                code="customer_deleted",
            )

        row = await self.audit.record(
            action=AuditAction.UPDATE,
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            summary=f"Admin note on customer {user.email}: {body.note[:80]}",
            metadata={"type": "admin_note", "note": body.note},
            request_id=request_id,
        )
        return CustomerActivityEvent(
            at=row.created_at,
            type=CustomerActivityEventType.NOTE_ADDED,
            actor_user_id=actor_user_id,
            summary=row.summary or "Admin note",
            metadata={"note": body.note},
        )

    # ======================================================================
    # Segment summary
    # ======================================================================

    async def segment_summary(self) -> CustomerSegmentSummary:
        agg = await self.repo.segment_summary()
        return CustomerSegmentSummary(**agg)

    # ======================================================================
    # Internal mapping
    # ======================================================================

    @staticmethod
    def _row_to_list_item(row: tuple) -> AdminCustomerListItem:
        user: User = row[0]
        orders_count = int(row[1])
        paid_count = int(row[2])
        ltv = Decimal(str(row[3] or 0))
        last_order = row[4]
        full_name = user.profile.full_name if user.profile else None
        return AdminCustomerListItem(
            id=user.id,
            clerk_user_id=user.clerk_user_id,
            email=user.email,
            phone=user.phone,
            full_name=full_name,
            status=user.status,
            is_deleted=user.deleted_at is not None,
            orders_count=orders_count,
            paid_orders_count=paid_count,
            lifetime_value=ltv,
            last_order_at=last_order,
            created_at=user.created_at,
        )

    def _to_detail(
        self,
        user: User,
        kpi_raw: dict,
        recent_orders: Sequence[Order],
    ) -> AdminCustomerDetail:
        profile: CustomerProfileRead | None = None
        if user.profile is not None:
            profile = CustomerProfileRead.model_validate(user.profile)

        addresses = [CustomerAddressRead.model_validate(a) for a in user.addresses]
        roles = [self._user_role_to_read(ur) for ur in user.roles]

        kpi = self._compute_kpi(user, kpi_raw)

        return AdminCustomerDetail(
            id=user.id,
            clerk_user_id=user.clerk_user_id,
            email=user.email,
            phone=user.phone,
            status=user.status,
            is_deleted=user.deleted_at is not None,
            created_at=user.created_at,
            updated_at=user.updated_at,
            deleted_at=user.deleted_at,
            profile=profile,
            addresses=addresses,
            roles=roles,
            kpi=kpi,
            recent_orders=[self._order_to_summary(o) for o in recent_orders],
        )

    @staticmethod
    def _compute_kpi(user: User, raw: dict) -> CustomerKPI:
        ltv = Decimal(str(raw["lifetime_value"] or 0))
        paid_count = int(raw["paid_orders_count"])
        aov = (
            (ltv / Decimal(paid_count)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if paid_count > 0
            else Decimal("0.00")
        )
        last_at: datetime | None = raw["last_order_at"]
        days_since = None
        if last_at is not None:
            now = datetime.now(UTC)
            # Both should be tz-aware (created_at is TIMESTAMPTZ); be defensive.
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=UTC)
            days_since = (now - last_at).days
            if days_since < 0:
                days_since = 0  # Clock skew defense
        first_at: datetime | None = raw["first_order_at"]
        account_age = (datetime.now(UTC) - user.created_at).days
        if account_age < 0:
            account_age = 0
        return CustomerKPI(
            orders_count=int(raw["orders_count"]),
            paid_orders_count=paid_count,
            cancelled_orders_count=int(raw["cancelled_orders_count"]),
            lifetime_value=ltv,
            average_order_value=aov,
            first_order_at=first_at,
            last_order_at=last_at,
            days_since_last_order=days_since,
            is_repeat=paid_count >= 2,
            account_age_days=account_age,
        )

    @staticmethod
    def _user_role_to_read(ur: UserRole) -> CustomerRoleRead:
        role: Role = ur.role
        return CustomerRoleRead(
            role_name=role.name,
            is_system=role.is_system,
            assigned_at=ur.created_at,
            assigned_by=ur.assigned_by,
        )

    @staticmethod
    def _order_to_summary(order: Order) -> CustomerOrderSummary:
        # `order.items` is lazy unless pre-loaded; `recent_orders` loads it.
        # `list_orders_for_user` also loads it. For the rare path that
        # doesn't, fall back to 0 — never trigger an async lazy load.
        try:
            item_count = len(order.items)
        except Exception:
            item_count = 0
        return CustomerOrderSummary(
            id=order.id,
            order_number=order.order_number,
            status=order.status.value,
            payment_status=order.payment_status.value,
            total=order.total,
            currency=order.currency,
            item_count=item_count,
            placed_at=order.placed_at,
            created_at=order.created_at,
        )

    @staticmethod
    def _audit_to_event(row: AuditLog) -> CustomerActivityEvent | None:
        meta = row.metadata_ or {}
        if meta.get("type") == "admin_note":
            return CustomerActivityEvent(
                at=row.created_at,
                type=CustomerActivityEventType.NOTE_ADDED,
                actor_user_id=row.actor_user_id,
                summary=row.summary or "Admin note",
                metadata=meta,
            )
        return None


# Silence type-checker for imports kept for clarity.
_ = (Address, UserProfile)
