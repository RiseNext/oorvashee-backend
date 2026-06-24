"""Domain enums.

All enums are Python `StrEnum` so they serialize cleanly through Pydantic and
JSON. Postgres-side they are stored as native ENUM types — defined in the
initial migration with deterministic names — so an invalid value is rejected
at the DB layer regardless of who writes to the table.

Adding a new value later requires `ALTER TYPE ... ADD VALUE` in a migration;
keep enums tight to avoid that ceremony for values you'll rarely use.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.dialects.postgresql import ENUM as PgEnum


def pg_enum(enum_cls: type[StrEnum], name: str) -> PgEnum:
    """Bind a Python StrEnum to a Postgres ENUM type already created by migration.

    Critically: `values_callable` forces SQLAlchemy to use the enum's `.value`
    (e.g. 'fabric') when serialising — not the default `.name` (e.g. 'FABRIC').
    The Postgres ENUM was declared with lowercase values in migration 0002, so
    omitting this produces `invalid input value for enum` errors at INSERT time.

    `create_type=False` keeps SQLAlchemy from trying to recreate the type;
    the migration owns its lifecycle.
    """
    return PgEnum(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda et: [e.value for e in et],
    )

# ---------------------------------------------------------------------------
# Identity / RBAC
# ---------------------------------------------------------------------------


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class RoleName(StrEnum):
    """Seeded role names. Free-form additions allowed in `roles.name`."""

    CUSTOMER = "customer"
    ADMIN = "admin"
    STAFF = "staff"
    # Delivery partner. Dispatch list + AWB entry ONLY — never admin/payment/PII.
    # Enforced server-side via require_role("courier"); seeded by migration 0011.
    COURIER = "courier"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class CategoryKind(StrEnum):
    FABRIC = "fabric"
    OCCASION = "occasion"
    REGION = "region"
    PRICE_BRACKET = "price_bracket"
    COLOR = "color"
    COLLECTION = "collection"


class ProductStatus(StrEnum):
    """Lifecycle. `archived` preserves slug → URL contract per PRD §7.2."""

    DRAFT = "draft"
    PUBLISHED = "published"
    UNAVAILABLE = "unavailable"  # auto when total stock = 0
    ARCHIVED = "archived"  # soft-deleted by admin; URL still resolves


# ---------------------------------------------------------------------------
# Commerce
# ---------------------------------------------------------------------------


class OrderStatus(StrEnum):
    PLACED = "placed"
    PACKED = "packed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"
    COD_PENDING = "cod_pending"


class PaymentMethod(StrEnum):
    RAZORPAY = "razorpay"
    COD = "cod"


class RazorpayPaymentStatus(StrEnum):
    """Mirrors Razorpay's `payments` resource states."""

    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class CouponKind(StrEnum):
    PERCENT = "percent"
    FLAT = "flat"


class StockMovementReason(StrEnum):
    ORDER_PLACED = "order_placed"
    ORDER_CANCELLED = "order_cancelled"
    MANUAL_ADJUSTMENT = "manual_adjustment"
    CSV_IMPORT = "csv_import"
    RESTOCK = "restock"


class CheckoutSessionStatus(StrEnum):
    """Lifecycle of a checkout_sessions row.

    ACTIVE -> PAYMENT_PROCESSING -> COMPLETED on success; EXPIRED via the
    advisory-lock cron when expires_at passes; CANCELLED if the user abandons.
    ACTIVE and PAYMENT_PROCESSING are the "active" states (one per user).
    """

    ACTIVE = "active"
    PAYMENT_PROCESSING = "payment_processing"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ReservationStatus(StrEnum):
    """Lifecycle of a reservations row.

    Only RESERVED + PAYMENT_PROCESSING (and expires_at > now()) count toward
    derived reserved stock. COMPLETED is set on confirmed payment; EXPIRED via
    cron; CANCELLED on session cancel.
    """

    RESERVED = "reserved"
    PAYMENT_PROCESSING = "payment_processing"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Engagement / system
# ---------------------------------------------------------------------------


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class BannerPlacement(StrEnum):
    HOMEPAGE_HERO = "homepage_hero"
    HOMEPAGE_SECONDARY = "homepage_secondary"
    CATEGORY_TOP = "category_top"
    CART_PROMO = "cart_promo"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    IN_APP = "in_app"


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    READ = "read"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGOUT = "logout"
    PAYMENT_CAPTURED = "payment_captured"
    STOCK_ADJUSTED = "stock_adjusted"
    STATUS_CHANGED = "status_changed"


# ---------------------------------------------------------------------------
# Helper: list of (enum_class, postgres_type_name) — single source of truth
# for the initial migration that creates the native ENUM types.
# ---------------------------------------------------------------------------


class ImportKind(StrEnum):
    """What an admin CSV upload is bulk-importing."""

    PRODUCT = "product"
    VARIANT = "variant"
    INVENTORY = "inventory"


class ImportStatus(StrEnum):
    """Lifecycle of an `import_jobs` row.

    `queued`  → row created, awaiting BackgroundTasks pickup.
    `running` → processor is iterating chunks. Status visible to admin.
    `completed` → finished — may have row errors but the job didn't crash.
    `failed` → catastrophic (CSV unreadable, DB connection lost mid-run).
                Partial commits up to the failure point ARE preserved.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


PG_ENUMS: list[tuple[type[StrEnum], str]] = [
    (UserStatus, "user_status"),
    (CategoryKind, "category_kind"),
    (ProductStatus, "product_status"),
    (OrderStatus, "order_status"),
    (PaymentStatus, "payment_status"),
    (PaymentMethod, "payment_method"),
    (RazorpayPaymentStatus, "razorpay_payment_status"),
    (CouponKind, "coupon_kind"),
    (StockMovementReason, "stock_movement_reason"),
    (ReviewStatus, "review_status"),
    (BannerPlacement, "banner_placement"),
    (NotificationChannel, "notification_channel"),
    (NotificationStatus, "notification_status"),
    (AuditAction, "audit_action"),
    (ImportKind, "import_kind"),
    (ImportStatus, "import_status"),
    (CheckoutSessionStatus, "checkout_session_status"),
    (ReservationStatus, "reservation_status"),
]
