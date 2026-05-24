"""ORM model registry.

Importing every model module here registers its table on `Base.metadata`,
which Alembic uses for autogenerate diffing and which the test suite relies
on for fixture creation.

Order matters only insofar as relationship strings (`"User"`, `"Product"`)
must be resolvable when SQLAlchemy compiles mappers — keeping imports
alphabetical avoids surprises.
"""

from app.db.base import (
    AuditableMixin,
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.address import Address
from app.models.audit_log import AuditLog
from app.models.banner import Banner
from app.models.cart import Cart, CartItem
from app.models.category import Category, ProductCategory
from app.models.coupon import Coupon
from app.models.inventory import Inventory, StockMovement
from app.models.notification import Notification
from app.models.order import Order, OrderItem
from app.models.payment import Payment, Shipment
from app.models.product import Product
from app.models.product_image import ProductImage
from app.models.product_variant import ProductVariant
from app.models.review import Review
from app.models.role import Role, UserRole
from app.models.user import User
from app.models.user_profile import UserProfile
from app.models.wishlist import Wishlist

__all__ = [
    "Address",
    "AuditLog",
    "AuditableMixin",
    "Banner",
    "Base",
    "Cart",
    "CartItem",
    "Category",
    "Coupon",
    "Inventory",
    "Notification",
    "Order",
    "OrderItem",
    "Payment",
    "Product",
    "ProductCategory",
    "ProductImage",
    "ProductVariant",
    "Review",
    "Role",
    "Shipment",
    "SoftDeleteMixin",
    "StockMovement",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "UserProfile",
    "UserRole",
    "Wishlist",
]
