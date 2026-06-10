"""CheckoutPaymentService — POST /checkout/{session_id}/pay.

Turns an ACTIVE, reserved checkout session into a PENDING order + a Razorpay
handoff ("keep order-at-checkout" decision):

  Phase A (one savepoint, row-locked):
    1. Lock the session row (owner + ACTIVE + not expired).
    2. Load its RESERVED reservations (the lines to pay for).
    3. SELECT ... FOR UPDATE the variants' inventory rows + final availability
       guard (variant active, product published, stock_on_hand >= qty).
    4. Build + persist the PENDING order + order_items (snapshots).
    5. Transition session + reservations RESERVED -> PAYMENT_PROCESSING and
       extend expires_at to now()+15min (the backstop window; the 3-min cron
       must never touch PAYMENT_PROCESSING).
  Phase B (no DB locks held):
    6. Create the Razorpay order + a Payment row (CREATED) and return the
       handoff. Physical stock is NOT decremented here — only on the capture
       webhook.

COD is intentionally out of scope (Razorpay-only redesign).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ReservationConflictError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.integrations.razorpay_client import RazorpayClient
from app.models.checkout_session import CheckoutSession
from app.models.enums import (
    CheckoutSessionStatus,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    ProductStatus,
    RazorpayPaymentStatus,
    ReservationStatus,
)
from app.models.order import Order, OrderItem
from app.models.payment import Payment
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.models.reservation import Reservation
from app.repositories.reservation_repo import ReservationRepository
from app.schemas.checkout import (
    CheckoutLineResult,
    PlaceOrderRead,
    RazorpayHandoff,
    StartPaymentRequest,
)
from app.services.base import BaseService
from app.services.inventory_service import InventoryService
from app.utils.ids import generate_order_number

log = get_logger(__name__)


class CheckoutPaymentService(BaseService):
    @property
    def reservations(self) -> ReservationRepository:
        return ReservationRepository(self.session)

    @property
    def inventory(self) -> InventoryService:
        return InventoryService(self.session)

    async def start_payment(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID,
        req: StartPaymentRequest,
    ) -> PlaceOrderRead:
        # --- Phase A: lock, validate, create order, transition --------------
        async with self.session.begin_nested():
            sess = await self._load_payable_session(session_id, user_id)
            reservations = await self.reservations.list_reserved_for_session(
                session_id
            )
            if not reservations:
                raise ReservationConflictError(
                    "This checkout session has no active reservation to pay for",
                    code="reservation_expired",
                )

            variant_ids = [r.variant_id for r in reservations]
            locks = await self.inventory.lock_inventory_rows(variant_ids)
            variants = await self._fetch_variants(variant_ids)

            order, items = self._build_order(
                sess=sess,
                reservations=reservations,
                variants=variants,
                locks=locks,
                req=req,
                user_id=user_id,
            )
            self.session.add(order)
            await self.session.flush()
            for item in items:
                item.order_id = order.id
                self.session.add(item)
            await self.session.flush()

            backstop = get_settings().reservation_payment_ttl_seconds
            new_expiry = utcnow() + timedelta(seconds=backstop)
            await self.session.execute(
                update(Reservation)
                .where(
                    Reservation.checkout_session_id == session_id,
                    Reservation.status == ReservationStatus.RESERVED,
                )
                .values(
                    status=ReservationStatus.PAYMENT_PROCESSING,
                    expires_at=new_expiry,
                    updated_at=func.now(),
                )
                .execution_options(synchronize_session=False)
            )
            sess.status = CheckoutSessionStatus.PAYMENT_PROCESSING
            sess.expires_at = new_expiry
            await self.session.flush()

        log.info(
            "checkout_payment_initiated",
            user_id=str(user_id),
            session_id=str(session_id),
            order_number=order.order_number,
            lines=len(items),
        )

        # --- Phase B: external Razorpay call (no DB locks held) -------------
        handoff = await self._initiate_razorpay(order)

        return PlaceOrderRead(
            order_number=order.order_number,
            status=order.status,
            payment_status=order.payment_status,
            payment_method=order.payment_method,
            total=order.total,
            currency=order.currency,
            payment=handoff,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_payable_session(
        self, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> CheckoutSession:
        sess = await self.reservations.get_session_for_update(session_id)
        if sess is None or sess.user_id != user_id:
            raise NotFoundError("Checkout session not found")
        if sess.status is CheckoutSessionStatus.PAYMENT_PROCESSING:
            raise ReservationConflictError(
                "A payment is already in progress for this checkout",
                code="payment_in_progress",
            )
        if sess.status is not CheckoutSessionStatus.ACTIVE:
            raise ReservationConflictError(
                "This checkout session is no longer active",
                code="reservation_expired",
            )
        if sess.expires_at <= utcnow():
            raise ReservationConflictError(
                "This checkout session has expired — please restart checkout",
                code="reservation_expired",
            )
        return sess

    async def _fetch_variants(
        self, variant_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ProductVariant]:
        if not variant_ids:
            return {}
        stmt = (
            select(ProductVariant)
            .where(ProductVariant.id.in_(variant_ids))
            .options(
                selectinload(ProductVariant.product).selectinload(Product.images),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.id: row for row in rows}

    def _build_order(
        self,
        *,
        sess: CheckoutSession,
        reservations,  # type: ignore[no-untyped-def]
        variants: dict[uuid.UUID, ProductVariant],
        locks,  # type: ignore[no-untyped-def]
        req: StartPaymentRequest,
        user_id: uuid.UUID,
    ) -> tuple[Order, list[OrderItem]]:
        items: list[OrderItem] = []
        subtotal = Decimal("0")
        failures: list[CheckoutLineResult] = []

        for res in reservations:
            variant = variants.get(res.variant_id)
            inv = locks.get(res.variant_id)
            product = variant.product if variant is not None else None
            stock = inv.stock if inv is not None else 0
            buyable = (
                variant is not None
                and variant.is_active
                and product is not None
                and product.status is ProductStatus.PUBLISHED
            )
            label_parts = (
                [p for p in (variant.color, variant.fabric) if p]
                if variant is not None
                else []
            )
            variant_label = " · ".join(label_parts) if label_parts else None

            if not buyable or stock < res.quantity:
                failures.append(
                    CheckoutLineResult(
                        variant_id=res.variant_id,
                        product_name=product.name if product else "Item",
                        variant_label=variant_label,
                        requested=res.quantity,
                        available=max(stock, 0),
                        reserved=False,
                        reason="unavailable" if not buyable else "out_of_stock",
                    )
                )
                continue

            unit_price = variant.price_override or product.base_price
            line_total = unit_price * res.quantity
            subtotal += line_total
            primary_image = next(
                (i.url for i in product.images if i.is_primary),
                product.images[0].url if product.images else None,
            )
            items.append(
                OrderItem(
                    product_id=product.id,
                    variant_id=variant.id,
                    product_name=product.name,
                    variant_label=variant_label,
                    sku=variant.sku,
                    primary_image_url=primary_image,
                    unit_price=unit_price,
                    quantity=res.quantity,
                    line_total=line_total,
                )
            )

        if failures:
            raise ReservationConflictError(
                "One or more items became unavailable before payment",
                extra={"lines": [f.model_dump(mode="json") for f in failures]},
            )

        shipping = tax = discount = Decimal("0")
        total = subtotal + shipping + tax - discount

        order = Order(
            order_number=generate_order_number(),
            user_id=user_id,
            checkout_session_id=sess.id,
            customer_name=req.customer.full_name,
            email=str(req.customer.email),
            phone=req.customer.phone,
            payment_method=PaymentMethod.RAZORPAY,
            subtotal=subtotal,
            shipping_amount=shipping,
            tax_amount=tax,
            discount_amount=discount,
            total=total,
            shipping_address=req.shipping_address.model_dump(),
            billing_address=(
                req.billing_address.model_dump() if req.billing_address else None
            ),
            notes=req.notes,
            status=OrderStatus.PLACED,
            payment_status=PaymentStatus.PENDING,
        )
        return order, items

    async def _initiate_razorpay(self, order: Order) -> RazorpayHandoff:
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
