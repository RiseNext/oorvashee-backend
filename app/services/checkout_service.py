"""CheckoutService — turns a cart into a placed order.

Two payment paths, one shared validate-and-lock prelude:

  Razorpay path:
    BEGIN TX
      1. Snapshot validate variants (existence, status, prices).
      2. SELECT ... FOR UPDATE on every inventory row (sorted ids).
      3. Pre-flight available stock.
      4. INSERT order (payment_status='pending').
      5. INSERT order_items (snapshotted from current product/variant).
      6. Increment `inventory.reserved` for each line + audit movement.
    COMMIT (locks released; no external call inside the tx).
    -- external --
      7. POST /orders to Razorpay (NOT inside the DB transaction).
      8. INSERT payment row with razorpay_order_id.
    Return Razorpay handoff payload to frontend.

  COD path:
    BEGIN TX
      1–5. Same as Razorpay.
      6. Decrement `inventory.stock` (no reservation) + audit movement.
      7. Set payment_status='cod_pending'.
    COMMIT.

Idempotency-Key middleware (app/core/idempotency.py) wraps the route
and replays the cached response for repeated keys — so a network retry
from the frontend never double-charges.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    OutOfStockError,
    ProductUnavailableError,
    ValidationError,
)
from app.core.logging import get_logger
from app.models.enums import (
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    RazorpayPaymentStatus,
)
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.repositories.cart_repo import CartRepository
from app.repositories.order_repo import OrderRepository, PaymentRepository
from app.schemas.checkout import (
    CheckoutAddress,
    CheckoutQuoteRead,
    CheckoutQuoteRequest,
    OrderItemRead,
    OrderRead,
    PlaceOrderRead,
    PlaceOrderRequest,
    QuoteLineRead,
    RazorpayHandoff,
    ShipmentRead,
)
from app.services.base import BaseService
from app.services.inventory_service import InventoryService, StockRequirement
from app.utils.ids import generate_order_number

log = get_logger(__name__)


class CheckoutService(BaseService):
    @property
    def cart_repo(self) -> CartRepository:
        return CartRepository(self.session)

    @property
    def orders(self) -> OrderRepository:
        return OrderRepository(self.session)

    @property
    def payments(self) -> PaymentRepository:
        return PaymentRepository(self.session)

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    # ------------------------------------------------------------------
    # Quote — no DB writes, no locks
    # ------------------------------------------------------------------

    async def quote(self, req: CheckoutQuoteRequest) -> CheckoutQuoteRead:
        variants = await self._fetch_variants([line.variant_id for line in req.items])
        items_read: list[QuoteLineRead] = []
        subtotal = Decimal("0")
        any_unavailable = False

        for line in req.items:
            variant = variants.get(line.variant_id)
            if variant is None:
                raise ValidationError(
                    f"Variant {line.variant_id} not found",
                    extra={"variant_id": str(line.variant_id)},
                )
            product = variant.product
            unit_price = variant.price_override or product.base_price
            line_total = unit_price * line.quantity
            available_stock = self._available_stock(variant)
            available = (
                product.status.value == "published"
                and variant.is_active
                and available_stock >= line.quantity
            )
            if not available:
                any_unavailable = True
            label_parts = [p for p in (variant.color, variant.fabric) if p]
            items_read.append(
                QuoteLineRead(
                    variant_id=variant.id,
                    product_name=product.name,
                    variant_label=" · ".join(label_parts) if label_parts else None,
                    unit_price=unit_price,
                    quantity=line.quantity,
                    line_total=line_total,
                    available=available,
                    available_stock=available_stock,
                )
            )
            subtotal += line_total

        # Shipping / tax / coupon left as zero for now — wire up when
        # PRD §15 open items (shipping zones, coupon admin) land.
        shipping = Decimal("0")
        tax = Decimal("0")
        discount = Decimal("0")
        total = subtotal + shipping + tax - discount

        return CheckoutQuoteRead(
            items=items_read,
            subtotal=subtotal,
            shipping_amount=shipping,
            tax_amount=tax,
            discount_amount=discount,
            total=total,
            has_unavailable_items=any_unavailable,
        )

    # ------------------------------------------------------------------
    # Place order — the critical section
    # ------------------------------------------------------------------

    async def place_order(
        self,
        req: PlaceOrderRequest,
        *,
        user_id: uuid.UUID | None,
    ) -> PlaceOrderRead:
        """Validate, lock, create order, reserve (or decrement) stock.

        Razorpay handoff (if applicable) is performed AFTER the DB
        transaction commits — never holds inventory row locks during
        external HTTP.
        """
        if not req.items:
            raise ValidationError("Order must contain at least one item")

        variants = await self._fetch_variants([line.variant_id for line in req.items])

        # --- Phase A: in a single transaction, lock + create order ----
        async with self.session.begin_nested():
            # Lock inventory rows in deterministic order
            requirements = [
                StockRequirement(variant_id=line.variant_id, quantity=line.quantity)
                for line in req.items
            ]

            # Build order skeleton with snapshot data BEFORE locking, so
            # the locking critical section is short.
            order, items, totals = self._build_order(
                req=req, variants=variants, user_id=user_id
            )
            self.session.add(order)
            await self.session.flush()
            for item in items:
                item.order_id = order.id
                self.session.add(item)
            await self.session.flush()

            # Now hit inventory.
            if req.payment_method == PaymentMethod.RAZORPAY:
                await self.inventory.reserve(
                    requirements, order_id=order.id, actor_user_id=user_id
                )
                order.payment_status = PaymentStatus.PENDING
            else:  # COD
                await self.inventory.decrement(
                    requirements, order_id=order.id, actor_user_id=user_id
                )
                order.payment_status = PaymentStatus.COD_PENDING

            await self.session.flush()

        log.info(
            "order_created",
            order_number=order.order_number,
            user_id=str(user_id) if user_id else None,
            method=req.payment_method.value,
            total=str(totals["total"]),
            line_count=len(items),
        )

        # --- Phase B: external Razorpay call (no DB locks held) -------
        razorpay_handoff: RazorpayHandoff | None = None
        if req.payment_method == PaymentMethod.RAZORPAY:
            razorpay_handoff = await self._initiate_razorpay(order)

        return PlaceOrderRead(
            order_number=order.order_number,
            status=order.status,
            payment_status=order.payment_status,
            payment_method=order.payment_method,
            total=order.total,
            currency=order.currency,
            payment=razorpay_handoff,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fetch_variants(
        self, variant_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ProductVariant]:
        if not variant_ids:
            return {}
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.id.in_(variant_ids))
            .options(
                selectinload(ProductVariant.inventory),
                selectinload(ProductVariant.product).selectinload(Product.images),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.id: row for row in rows}

    @staticmethod
    def _available_stock(variant: ProductVariant) -> int:
        inv = variant.inventory
        if inv is None:
            return 0
        return max(inv.stock - inv.reserved, 0)

    def _build_order(
        self,
        *,
        req: PlaceOrderRequest,
        variants: dict[uuid.UUID, ProductVariant],
        user_id: uuid.UUID | None,
    ) -> tuple[Order, list[OrderItem], dict[str, Decimal]]:
        """Construct (not persist) the order + line items with snapshots.

        Validates product status + price serverside (never trusts client).
        """
        items: list[OrderItem] = []
        subtotal = Decimal("0")

        for line in req.items:
            variant = variants.get(line.variant_id)
            if variant is None or not variant.is_active:
                raise ValidationError(
                    f"Variant {line.variant_id} not available",
                    extra={"variant_id": str(line.variant_id)},
                )
            product = variant.product
            if product.status.value != "published":
                raise ProductUnavailableError(
                    f"'{product.name}' is no longer available",
                    extra={"variant_id": str(variant.id)},
                )
            # Soft pre-flight; the real check is under FOR UPDATE in inventory.
            if line.quantity > self._available_stock(variant):
                raise OutOfStockError(
                    f"'{product.name}' has insufficient stock",
                    extra={
                        "variant_id": str(variant.id),
                        "requested": line.quantity,
                        "available": self._available_stock(variant),
                    },
                )

            unit_price = variant.price_override or product.base_price
            line_total = unit_price * line.quantity
            subtotal += line_total

            primary_image = next(
                (i.url for i in product.images if i.is_primary),
                product.images[0].url if product.images else None,
            )
            label_parts = [p for p in (variant.color, variant.fabric) if p]

            items.append(
                OrderItem(
                    product_id=product.id,
                    variant_id=variant.id,
                    product_name=product.name,
                    variant_label=" · ".join(label_parts) if label_parts else None,
                    sku=variant.sku,
                    primary_image_url=primary_image,
                    unit_price=unit_price,
                    quantity=line.quantity,
                    line_total=line_total,
                )
            )

        shipping = Decimal("0")
        tax = Decimal("0")
        discount = Decimal("0")
        total = subtotal + shipping + tax - discount

        order = Order(
            order_number=generate_order_number(),
            user_id=user_id,
            customer_name=req.customer.full_name,
            email=str(req.customer.email),
            phone=req.customer.phone,
            payment_method=req.payment_method,
            subtotal=subtotal,
            shipping_amount=shipping,
            tax_amount=tax,
            discount_amount=discount,
            total=total,
            shipping_address=_address_to_dict(req.shipping_address),
            billing_address=_address_to_dict(req.billing_address) if req.billing_address else None,
            notes=req.notes,
            status=OrderStatus.PLACED,
        )

        return order, items, {
            "subtotal": subtotal,
            "shipping": shipping,
            "tax": tax,
            "discount": discount,
            "total": total,
        }

    async def _initiate_razorpay(self, order: Order) -> RazorpayHandoff:
        from app.core.config import get_settings
        from app.integrations.razorpay_client import RazorpayClient

        settings = get_settings()
        client = RazorpayClient(settings)
        rzp_order = await client.create_order(
            amount=order.total,
            currency=order.currency,
            receipt=order.order_number,
            notes={"order_id": str(order.id), "order_number": order.order_number},
        )

        payment = Payment(
            order_id=order.id,
            razorpay_order_id=rzp_order.id,
            amount=order.total,
            currency=order.currency,
            status=RazorpayPaymentStatus.CREATED,
        )
        self.session.add(payment)
        await self.session.flush()

        return RazorpayHandoff(
            razorpay_order_id=rzp_order.id,
            razorpay_key_id=client.key_id,
            amount_paise=rzp_order.amount_paise,
            currency=rzp_order.currency,
        )

    # ------------------------------------------------------------------
    # Read serializer (used by /orders endpoints too)
    # ------------------------------------------------------------------

    @staticmethod
    def serialize_order(order: Order) -> OrderRead:
        shipment_read = None
        if order.shipment:
            shipment_read = ShipmentRead.model_validate(order.shipment)
        return OrderRead(
            id=order.id,
            order_number=order.order_number,
            status=order.status,
            payment_status=order.payment_status,
            payment_method=order.payment_method,
            customer_name=order.customer_name,
            email=order.email,
            phone=order.phone,
            items=[OrderItemRead.model_validate(it) for it in order.items],
            subtotal=order.subtotal,
            shipping_amount=order.shipping_amount,
            tax_amount=order.tax_amount,
            discount_amount=order.discount_amount,
            total=order.total,
            currency=order.currency,
            shipping_address=order.shipping_address,
            billing_address=order.billing_address,
            notes=order.notes,
            placed_at=order.placed_at,
            packed_at=order.packed_at,
            shipped_at=order.shipped_at,
            delivered_at=order.delivered_at,
            cancelled_at=order.cancelled_at,
            created_at=order.created_at,
            shipment=shipment_read,
        )


def _address_to_dict(addr: CheckoutAddress) -> dict:
    return addr.model_dump()
