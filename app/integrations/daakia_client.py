"""Thin async wrapper over the Daakia courier API — TRACKING ONLY.

Mirrors razorpay_client.py / clerk_management.py: raw httpx, no SDK, secrets from
config, IntegrationError on failure. We use ONLY the read-safe endpoints:
  - POST /api/v1/DaakiaApi/token          (auth — cached, daily refresh)
  - POST /api/v1/DaakiaApi/track-shipment (live tracking by vendorId + awb)
  - GET  /api/v1/DaakiaApi/vendor-list     (courier vendor dropdown)

We deliberately do NOT call book-shipment, print-label, cancel-shipment, or
pickup-list — the courier books outside our system; we only DISPLAY tracking.
All calls run on the backend (Railway, IP-whitelisted) — never the browser.

Daakia envelope: {RequestId, ResponseMessage, ResponseCode, Data}; ResponseCode
"000" == success. The access_token expires daily at 11:59 PM server time, so it
is cached PER PROCESS and refreshed on/just-before expiry — never per request.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationError
from app.core.logging import get_logger

log = get_logger(__name__)

# Fast-fail timeout so a slow/unresponsive Daakia never hangs a customer page.
DAAKIA_TIMEOUT_SECONDS = 6.0
_TOKEN_REFRESH_MARGIN = timedelta(minutes=5)
_VENDOR_CACHE_TTL = timedelta(minutes=10)

_AUTH_PATH = "/api/v1/DaakiaApi/token"
_TRACK_PATH = "/api/v1/DaakiaApi/track-shipment"
_VENDOR_PATH = "/api/v1/DaakiaApi/vendor-list"


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DaakiaTrackingEvent:
    message: str | None
    location: str | None
    ship_status: str | None
    event_time: str | None


@dataclass(frozen=True, slots=True)
class DaakiaTracking:
    awb_number: str | None
    vendor_name: str | None
    last_status: str | None
    tracking_url: str | None
    events: list[DaakiaTrackingEvent]

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> DaakiaTracking:
        events = [
            DaakiaTrackingEvent(
                message=e.get("message"),
                location=e.get("location") or None,
                ship_status=e.get("shipStatus"),
                event_time=e.get("eventTime"),
            )
            for e in (data.get("TrackingList") or [])
            if isinstance(e, dict)
        ]
        return cls(
            awb_number=data.get("awbNumber"),
            vendor_name=data.get("vendorName"),
            last_status=data.get("lastStatus"),
            tracking_url=data.get("trackingUrl"),
            events=events,
        )


@dataclass(frozen=True, slots=True)
class DaakiaVendor:
    vendor_id: int
    vendor_code: str | None
    vendor_name: str | None


# --------------------------------------------------------------------------- #
# Per-process caches (async-safe)
# --------------------------------------------------------------------------- #
@dataclass
class _CachedToken:
    token: str
    expires_at: datetime  # UTC


class _TokenCache:
    def __init__(self) -> None:
        self._entries: dict[str, _CachedToken] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str, fetch: Any) -> str:
        now = datetime.now(UTC)
        entry = self._entries.get(key)
        if entry and entry.expires_at - _TOKEN_REFRESH_MARGIN > now:
            return entry.token
        async with self._lock:
            entry = self._entries.get(key)
            if entry and entry.expires_at - _TOKEN_REFRESH_MARGIN > now:
                return entry.token
            token, expires_at = await fetch()
            self._entries[key] = _CachedToken(token=token, expires_at=expires_at)
            log.info("daakia_token_refreshed", expires_at=expires_at.isoformat())
            return token


@dataclass
class _CachedVendors:
    vendors: list[DaakiaVendor]
    fetched_at: datetime


class _VendorCache:
    def __init__(self) -> None:
        self._entry: _CachedVendors | None = None
        self._lock = asyncio.Lock()

    async def get(self, fetch: Any) -> list[DaakiaVendor]:
        now = datetime.now(UTC)
        entry = self._entry
        if entry and now - entry.fetched_at < _VENDOR_CACHE_TTL:
            return entry.vendors
        async with self._lock:
            entry = self._entry
            if entry and now - entry.fetched_at < _VENDOR_CACHE_TTL:
                return entry.vendors
            vendors = await fetch()
            self._entry = _CachedVendors(vendors=vendors, fetched_at=now)
            return vendors


_token_cache = _TokenCache()
_vendor_cache = _VendorCache()


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class DaakiaClient:
    """Stateless per-call client; token + vendor caches are module-level."""

    def __init__(self, settings: Settings) -> None:
        self._base = str(settings.daakia_base_url).rstrip("/")
        self._client_id = settings.daakia_client_id
        self._client_secret = settings.daakia_client_secret

    def _http(self, *, token: str | None = None) -> httpx.AsyncClient:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(
            base_url=self._base, headers=headers, timeout=DAAKIA_TIMEOUT_SECONDS
        )

    # ---------- Auth (cached, daily refresh) ----------
    async def _token(self) -> str:
        return await _token_cache.get(self._client_id, self._fetch_token)

    async def _fetch_token(self) -> tuple[str, datetime]:
        try:
            async with self._http() as client:
                resp = await client.post(
                    _AUTH_PATH,
                    json={
                        "clientId": self._client_id,
                        "clientSecret": self._client_secret,
                    },
                )
        except httpx.RequestError as exc:
            log.exception("daakia_token_request_error")
            raise IntegrationError(
                "Daakia token request failed", code="daakia_unreachable"
            ) from exc
        data = self._unwrap(resp, "daakia_token_failed") or {}
        token = data.get("access_token")
        if not token:
            raise IntegrationError(
                "Daakia token missing in response", code="daakia_token_missing"
            )
        return str(token), self._token_expiry(data)

    @staticmethod
    def _token_expiry(data: dict[str, Any]) -> datetime:
        # Prefer the server-provided expiry_utc (the daily 11:59 PM cutoff); fall
        # back to issued + expires_in; finally a conservative 30 minutes.
        raw = data.get("expiry_utc")
        if isinstance(raw, str) and raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                pass
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            return datetime.now(UTC) + timedelta(seconds=float(expires_in))
        return datetime.now(UTC) + timedelta(minutes=30)

    # ---------- Tracking ----------
    async def track_shipment(self, vendor_id: int | str, awb: str) -> DaakiaTracking:
        token = await self._token()
        try:
            async with self._http(token=token) as client:
                resp = await client.post(
                    _TRACK_PATH,
                    json={"vendorId": str(vendor_id), "awb_number": awb},
                )
        except httpx.RequestError as exc:
            log.exception("daakia_track_request_error")
            raise IntegrationError(
                "Daakia tracking request failed", code="daakia_unreachable"
            ) from exc
        data = self._unwrap(resp, "daakia_track_failed") or {}
        return DaakiaTracking.from_data(data)

    # ---------- Vendors (cached briefly) ----------
    async def list_vendors(self) -> list[DaakiaVendor]:
        return await _vendor_cache.get(self._fetch_vendors)

    async def _fetch_vendors(self) -> list[DaakiaVendor]:
        token = await self._token()
        try:
            async with self._http(token=token) as client:
                resp = await client.get(_VENDOR_PATH)
        except httpx.RequestError as exc:
            log.exception("daakia_vendor_request_error")
            raise IntegrationError(
                "Daakia vendor list failed", code="daakia_unreachable"
            ) from exc
        data = self._unwrap(resp, "daakia_vendor_list_failed")
        out: list[DaakiaVendor] = []
        for v in data or []:
            if not isinstance(v, dict):
                continue
            try:
                out.append(
                    DaakiaVendor(
                        vendor_id=int(v.get("VendorId")),
                        vendor_code=v.get("VendorCode"),
                        vendor_name=v.get("VendorName"),
                    )
                )
            except (TypeError, ValueError):
                continue
        return out

    # ---------- Envelope ----------
    @staticmethod
    def _unwrap(resp: httpx.Response, error_code: str) -> Any:
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            log.warning(error_code, status=resp.status_code, body=resp.text[:200])
            raise IntegrationError(
                "Daakia API returned an unexpected response", code=error_code
            )
        rc = str(payload.get("ResponseCode", ""))
        if resp.status_code >= 400 or rc != "000":
            msg = payload.get("ResponseMessage") or "Daakia API error"
            log.warning(error_code, status=resp.status_code, rc=rc, message=msg)
            raise IntegrationError(str(msg), code=error_code)
        return payload.get("Data")
