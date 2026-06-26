"""Customer-facing live courier tracking — clean, PII-free projection.

Returned by the authenticated account tracking endpoint AND the email-gated guest
tracking endpoint. Carries ONLY courier movement data (awb, carrier, status, scan
events, the carrier's tracking URL) — never payment, address, phone, or email.
"""
from __future__ import annotations

from pydantic import BaseModel


class TrackingEvent(BaseModel):
    message: str | None = None
    location: str | None = None
    status: str | None = None  # Daakia shipStatus (e.g. FORWARD)
    event_time: str | None = None  # raw Daakia eventTime string


class OrderTracking(BaseModel):
    awb: str | None = None
    vendor_name: str | None = None
    last_status: str | None = None
    tracking_url: str | None = None
    events: list[TrackingEvent] = []
    # True when LIVE Daakia data was fetched; False when there is no awb/vendor
    # yet, or Daakia was unreachable/errored (the UI falls back gracefully).
    available: bool = False
