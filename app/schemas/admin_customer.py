"""Admin customer schemas — operations CRM view.

Privacy posture:
- Email + phone are visible to admins (operational requirement — ops must
  be able to reach a customer about their order). The router gates these
  endpoints behind `require_role('admin')`.
- Soft-deleted users (Clerk webhook `user.deleted` flow) appear as
  `is_deleted=true` with email scrubbed to `deleted+<id>@oorvashee.invalid`
  and the profile fields blanked. Order history stays attached so the
  audit trail survives.
- We never expose Clerk session tokens, password hashes (none — Clerk
  owns auth), or raw JWT claims through these endpoints.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserStatus

# ---------- Filters / sort -----------------------------------------------


class CustomerListSort(StrEnum):
    """Stable orderings for the admin customers table."""

    NEWEST = "newest"          # users.created_at desc
    OLDEST = "oldest"
    LIFETIME_VALUE_DESC = "ltv_desc"
    ORDERS_DESC = "orders_desc"
    LAST_ORDER_DESC = "last_order_desc"


# ---------- List shape ----------------------------------------------------


class AdminCustomerListItem(BaseModel):
    """Lean row for the admin customers table.

    `lifetime_value` is the sum of `orders.total` over PAID orders only.
    `orders_count` includes every order in any state (including cancelled
    / pending) so the admin can spot abandoned-cart customers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clerk_user_id: str
    email: str
    phone: str | None
    full_name: str | None
    status: UserStatus
    is_deleted: bool = Field(description="users.deleted_at IS NOT NULL — PII-scrubbed.")
    orders_count: int
    paid_orders_count: int
    lifetime_value: Decimal
    last_order_at: datetime | None
    created_at: datetime


# ---------- Role / address shapes for detail view -------------------------


class CustomerRoleRead(BaseModel):
    """One assigned role + the admin who assigned it (when known)."""

    model_config = ConfigDict(from_attributes=True)

    role_name: str
    is_system: bool
    assigned_at: datetime
    assigned_by: uuid.UUID | None


class CustomerAddressRead(BaseModel):
    """Mailing address. We expose this to admin so order disputes can be
    cross-checked, but the admin UI should mark it 'PII — handle carefully'."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str | None
    recipient_name: str
    phone: str
    line1: str
    line2: str | None
    city: str
    state: str
    postal_code: str
    country: str
    is_default: bool
    created_at: datetime


class CustomerProfileRead(BaseModel):
    """Subset of `user_profiles` admin needs."""

    model_config = ConfigDict(from_attributes=True)

    full_name: str | None
    display_name: str | None
    avatar_url: str | None
    date_of_birth: date | None
    gender: str | None
    locale: str


# ---------- Customer KPI tile --------------------------------------------


class CustomerKPI(BaseModel):
    """Per-customer KPIs — drive the detail page's tile row.

    `days_since_last_order` is null when the customer has never ordered.
    Distinct from "0 days" which means they ordered today.
    """

    model_config = ConfigDict(from_attributes=True)

    orders_count: int
    paid_orders_count: int
    cancelled_orders_count: int
    lifetime_value: Decimal
    average_order_value: Decimal = Field(
        description="`lifetime_value / paid_orders_count`, 0 when no paid orders. 2dp."
    )
    first_order_at: datetime | None
    last_order_at: datetime | None
    days_since_last_order: int | None
    is_repeat: bool = Field(description="`paid_orders_count >= 2`.")
    account_age_days: int


# ---------- Order summary for the detail page ----------------------------


class CustomerOrderSummary(BaseModel):
    """Compact order row used inside the customer detail response.

    Distinct from `AdminOrderListItem` — admin's customer view doesn't
    need columns the orders table already provides (customer_name, email).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_number: str
    status: str
    payment_status: str
    total: Decimal
    currency: str
    item_count: int
    placed_at: datetime | None
    created_at: datetime


# ---------- Activity timeline --------------------------------------------


class CustomerActivityEventType(StrEnum):
    SIGNED_UP = "signed_up"
    ORDER_PLACED = "order_placed"
    ORDER_PAID = "order_paid"
    ORDER_CANCELLED = "order_cancelled"
    ROLE_ASSIGNED = "role_assigned"
    NOTE_ADDED = "note_added"


class CustomerActivityEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    type: CustomerActivityEventType
    actor_user_id: uuid.UUID | None
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------- Detail --------------------------------------------------------


class AdminCustomerDetail(BaseModel):
    """Full admin view of one customer."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clerk_user_id: str
    email: str
    phone: str | None
    status: UserStatus
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    profile: CustomerProfileRead | None
    addresses: list[CustomerAddressRead]
    roles: list[CustomerRoleRead]

    kpi: CustomerKPI
    recent_orders: list[CustomerOrderSummary]


# ---------- Add-note payload + response ----------------------------------


class AddCustomerNoteRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    note: str = Field(min_length=1, max_length=2000)


# ---------- Segment / summary dashboard tile ------------------------------


class CustomerSegmentSummary(BaseModel):
    """Counters for the admin customers dashboard.

    Definitions match the analytics service's customer_metrics
    (Phase 3E) — same semantics keep the two screens reconcilable.
    """

    model_config = ConfigDict(from_attributes=True)

    total_customers: int = Field(description="`users` with `deleted_at IS NULL`.")
    customers_with_orders: int = Field(description="Distinct user_id appearing on any order.")
    customers_with_paid_orders: int = Field(description="Distinct user_id with ≥1 paid order.")
    repeat_customers: int = Field(description="user_id with ≥2 paid orders.")
    new_last_30_days: int = Field(description="users.created_at within last 30 days.")
    dormant_customers: int = Field(
        description="Has at least one paid order, last_order_at older than 90 days."
    )
    deleted_customers: int = Field(description="users.deleted_at IS NOT NULL (PII-scrubbed).")
