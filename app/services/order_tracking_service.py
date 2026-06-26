"""OrderTrackingService — live courier tracking for a customer's order.

Given an order that already has a stored AWB + Daakia vendor (set by the courier
on /courier), calls Daakia's track-shipment and returns a clean, PII-free
projection. On ANY Daakia failure/timeout it falls back to `available=False` with
the stored awb/vendor so the order page degrades gracefully instead of erroring.

We NEVER book or cancel — only the read-only track-shipment call is made here.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger
from app.integrations.daakia_client import DaakiaClient
from app.models.order import Order
from app.schemas.tracking import OrderTracking, TrackingEvent
from app.services.base import BaseService

log = get_logger(__name__)


class OrderTrackingService(BaseService):
    async def for_order(self, order: Order) -> OrderTracking:
        shipment = order.shipment
        awb = shipment.tracking_id if shipment else None
        vendor_id = shipment.courier_vendor_id if shipment else None
        vendor_name = shipment.courier_vendor_name if shipment else None
        stored_url = shipment.tracking_url if shipment else None

        # No AWB or vendor yet → nothing live to fetch (page shows the stored card).
        if not awb or not vendor_id:
            return OrderTracking(
                awb=awb,
                vendor_name=vendor_name,
                tracking_url=stored_url,
                available=False,
            )

        try:
            tracking = await DaakiaClient(get_settings()).track_shipment(vendor_id, awb)
        except IntegrationError as exc:
            # Slow/unreachable/not-found → graceful fallback, never a 5xx.
            log.info(
                "daakia_tracking_unavailable",
                order_number=order.order_number,
                code=getattr(exc, "code", None),
            )
            return OrderTracking(
                awb=awb,
                vendor_name=vendor_name,
                tracking_url=stored_url,
                available=False,
            )

        return OrderTracking(
            awb=tracking.awb_number or awb,
            vendor_name=tracking.vendor_name or vendor_name,
            last_status=tracking.last_status,
            tracking_url=tracking.tracking_url or stored_url,
            events=[
                TrackingEvent(
                    message=e.message,
                    location=e.location,
                    status=e.ship_status,
                    event_time=e.event_time,
                )
                for e in tracking.events
            ],
            available=True,
        )
