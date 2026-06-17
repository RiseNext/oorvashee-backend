"""Client for Oorvashee separate POS print middleware.

This module intentionally keeps printing best-effort. If the print middleware
or the store PC printer is temporarily unavailable, the live payment/order flow
must not fail.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib import request


def _to_number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_attr(obj: Any, names: list[str], default: Any = None) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _dict_value(data: Any, names: list[str], default: Any = None) -> Any:
    if isinstance(data, dict):
        for name in names:
            value = data.get(name)
            if value not in (None, ""):
                return value
    return default


def _customer_payload(order: Any) -> dict[str, str]:
    """Extract customer name/phone safely from different possible order shapes."""
    shipping = _get_attr(order, ["shipping_address", "shipping", "delivery_address"], {}) or {}
    billing = _get_attr(order, ["billing_address", "billing"], {}) or {}

    name = (
        _get_attr(order, ["customer_name", "name", "buyer_name"], None)
        or _dict_value(shipping, ["name", "full_name", "customer_name"], None)
        or _dict_value(billing, ["name", "full_name", "customer_name"], None)
        or "Customer"
    )
    phone = (
        _get_attr(order, ["customer_phone", "phone", "mobile", "buyer_phone"], None)
        or _dict_value(shipping, ["phone", "mobile", "customer_phone"], None)
        or _dict_value(billing, ["phone", "mobile", "customer_phone"], None)
        or ""
    )
    return {"name": str(name), "phone": str(phone)}


def _item_name(item: Any) -> str:
    product = _get_attr(item, ["product", "variant"], None)
    return str(
        _get_attr(item, ["product_name", "variant_name", "name", "title", "sku"], None)
        or _get_attr(product, ["name", "title", "sku"], None)
        or "Item"
    )


def _items_payload(order: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in getattr(order, "items", []) or []:
        qty = int(_to_number(_get_attr(item, ["quantity", "qty"], 1), 1))
        price = _to_number(
            _get_attr(item, ["unit_price", "price", "selling_price", "rate"], 0),
            0,
        )
        amount = _to_number(
            _get_attr(item, ["line_total", "total", "amount", "subtotal"], None),
            qty * price,
        )
        rows.append(
            {
                "name": _item_name(item),
                "qty": qty,
                "price": price,
                "amount": amount,
            }
        )
    return rows


def build_print_payload(order: Any, razorpay_payment_id: str | None = None) -> dict[str, Any]:
    order_number = str(_get_attr(order, ["order_number", "number", "id"], "ORDER"))
    items = _items_payload(order)

    total = _to_number(
        _get_attr(
            order,
            [
                "grand_total",
                "total_amount",
                "payable_amount",
                "amount_total",
                "total",
            ],
            None,
        ),
        sum(_to_number(i.get("amount")) for i in items),
    )

    created_at = _get_attr(order, ["placed_at", "created_at", "updated_at"], None)
    if created_at is None:
        created_at = datetime.now(UTC)
    date_text = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at)

    return {
        "external_ref": order_number,
        "idempotency_key": f"{order_number}-PAID-PRINT-v1",
        "job_type": "invoice",
        "payload": {
            "bill_no": order_number,
            "order_number": order_number,
            "date": date_text,
            "customer": _customer_payload(order),
            "items": items,
            "total": total,
            "payment": "PAID",
            "payment_id": razorpay_payment_id,
        },
    }


def _post_json(url: str, api_key: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, default=str).encode("utf-8")
    req = request.Request(  # noqa: S310 - URL comes from configured env, not user input
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-api-key": api_key,
        },
    )
    with request.urlopen(req, timeout=5) as resp:  # noqa: S310 - URL is configured env
        if resp.status >= 400:
            raise RuntimeError(f"Print service returned HTTP {resp.status}")


async def send_order_to_print_service(
    *,
    order: Any,
    razorpay_payment_id: str | None = None,
) -> None:
    """Create a print job for a paid order.

    Env required on Oorvashee backend Railway:
      PRINT_SERVICE_URL=https://oorvashee-print-service-production.up.railway.app
      PRINT_SERVICE_API_KEY=<your print service key>
    """
    base_url = (os.getenv("PRINT_SERVICE_URL") or "").rstrip("/")
    api_key = os.getenv("PRINT_SERVICE_API_KEY") or ""

    if not base_url or not api_key:
        # Missing env should not break payment flow. Log is done by caller.
        raise RuntimeError("PRINT_SERVICE_URL or PRINT_SERVICE_API_KEY is missing")

    payload = build_print_payload(order, razorpay_payment_id=razorpay_payment_id)
    url = f"{base_url}/api/print/jobs"

    await asyncio.to_thread(_post_json, url, api_key, payload)
