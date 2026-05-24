# INVENTORY_FLOW.md

> **Status:** PLANNED — no inventory code exists yet. This document defines the target stock-management flow derived strictly from PRD §6.3 and §6.1.

---

## 1. PRD-Locked Rules

| Rule | Source |
|---|---|
| Stock is tracked **per variant**, not per product | PRD §6.1, §6.3 |
| Stock auto-decrements on order | PRD §6.3 |
| When all variants' stock = 0, product is auto-marked unavailable | PRD §6.3 |
| Low-stock alert on admin dashboard | PRD §6.3 (SHOULD HAVE) |
| Single warehouse / no multi-location | Implied (no mention of multi-location) |

---

## 2. Entities Involved

From [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md):

- `product_variant` — the unit of stock. Columns: `stock`, `low_stock_threshold`, `is_active`.
- `product` — derived availability via the `status` column.
- `stock_movement` — append-only audit log of every change.

---

## 3. Stock State Machine

```
                  ┌────────────────────────────────────────────────┐
                  │  product_variant.stock                         │
                  │                                                │
                  │   N  ── order placed (Razorpay path) ──>  N−q  │
                  │   N  ── order placed (COD path) ──────>  N−q  │
                  │   N  ── order cancelled ─────────────>  N+q  │
                  │   N  ── manual restock by admin ─────>  N+x  │
                  │   N  ── manual adjustment (write-off) >  N−x  │
                  │   N  ── CSV import ─────────────────>   N±x   │
                  └────────────────────────────────────────────────┘

                              ▼ on every change ▼

                  ┌────────────────────────────────────────────────┐
                  │  stock_movement (audit row)                    │
                  │   variant_id, delta, reason, order_id, actor  │
                  └────────────────────────────────────────────────┘
```

Stock can never go negative — DB CHECK constraint `stock >= 0` plus a service-level pre-flight check.

---

## 4. Order → Stock Decrement: Two Paths

### 4.1 Razorpay (online payments)

```
1. POST /checkout/orders
   ├── Stock check: for each variant, SELECT stock FROM product_variant WHERE id = :id FOR UPDATE
   │   (row-level lock prevents concurrent oversell)
   ├── If any variant insufficient → 409 with { unavailable_items: [...] }, no order written
   ├── INSERT order (payment_status = 'pending')
   ├── INSERT order_items
   ├── Create Razorpay order
   └── DO NOT decrement stock yet  ←──┐
                                      │
2. Customer pays in Razorpay widget   │
                                      │
3a. POST /payments/verify (frontend)  │
3b. POST /webhooks/razorpay (Meta)    │  Either one triggers finalisation
                                      │  (idempotent via webhook_event_id + Idempotency-Key)
4. payment_service.finalize(order):   │
   ├── Verify HMAC signature           │
   ├── If payment.status = 'captured'  ▼
   │   ├── BEGIN TRANSACTION
   │   ├── For each order_item:
   │   │     UPDATE product_variant SET stock = stock - quantity WHERE id = :id
   │   │     INSERT stock_movement (variant_id, delta = -quantity, reason='order_placed', order_id=...)
   │   ├── Update order.payment_status = 'paid'
   │   ├── product_availability_service.recompute(product_id)
   │   │     (sets product.status='unavailable' if all variants now 0)
   │   ├── Queue confirmation email (Resend)
   │   └── COMMIT
   └── If payment failed → order remains 'pending'/'failed'. Stock untouched. Order auto-cancels after 30 min (cleanup job, Phase 2).
```

**Why decrement on payment, not on order create?** Avoids reserving stock for customers who close the Razorpay window. For a low-volume luxury catalog with mostly single-unit sales, this is fine. If oversell becomes a problem (rare), add a 15-minute soft reservation in Phase 2.

### 4.2 COD (Cash on Delivery)

```
1. POST /checkout/orders with payment_method = 'cod'
   ├── Stock check with FOR UPDATE
   ├── If insufficient → 409
   ├── INSERT order (payment_status = 'cod_pending')
   ├── INSERT order_items
   ├── DECREMENT stock immediately   ←── COD has no payment gate, so reserve on order
   ├── INSERT stock_movement
   ├── Recompute product availability
   ├── Queue confirmation email
   └── 201 Created
```

COD payment is marked `paid` later via admin endpoint `POST /admin/orders/{number}/cod/mark-paid` — no stock effect there.

### 4.3 Order Cancellation

`POST /admin/orders/{number}/cancel`:
- If `order.status not in (delivered)`: restock all line items, INSERT `stock_movement` with `reason='order_cancelled'`, update `order.status='cancelled'`, recompute product availability.
- If already delivered: cannot cancel. Refund flow is Phase 2.

---

## 5. Product Availability Recomputation

After every stock change (decrement or restock):

```python
# inventory_service.recompute_product_availability(product_id)

total_stock = SUM(stock) over all active variants of product
if total_stock == 0 and product.status == 'published':
    product.status = 'unavailable'
    log: "auto-marked unavailable, all variants at 0"
elif total_stock > 0 and product.status == 'unavailable':
    product.status = 'published'
    log: "auto-restored to published, stock returned"
```

`unavailable` differs from `archived`:
- `unavailable` = no stock; URL still resolves; can be auto-restored when restocked
- `archived` = admin-deleted; URL still resolves to "Product Unavailable"; never auto-restored

---

## 6. Low-Stock Alerts

PRD §6.3 — SHOULD HAVE.

- Threshold per variant: `product_variant.low_stock_threshold` (default 2).
- Admin endpoint: `GET /admin/inventory/low-stock` returns variants where `0 < stock <= low_stock_threshold`.
- Dashboard badge shows the count.
- **No emails / push notifications** at launch — purely a dashboard flag (matches PRD scope; emails are noise).

---

## 7. Manual Adjustments

Admin endpoint: `POST /admin/inventory/variants/{id}/adjust`.

Body:
```json
{ "delta": -1, "reason": "manual_adjustment", "note": "Damaged in showroom display" }
```

Writes:
- UPDATE `product_variant.stock` (with CHECK >= 0 enforcement)
- INSERT `stock_movement` with `actor_user_id = current admin`
- Recompute product availability

---

## 8. CSV Import Stock Behaviour

Per PRD §6.1: bulk upload via CSV.

The CSV format includes `stock` per variant. Import logic:

- For **new variants** (no matching SKU): INSERT with the CSV stock, write `stock_movement` `reason='csv_import'`, `delta = stock`.
- For **existing variants** (SKU match): two modes (admin chooses at upload time):
  - **Replace** — `stock = csv_value`; log delta accordingly.
  - **Add** — `stock = stock + csv_value`; log positive delta.
- Default to **Replace** unless explicitly toggled.

CSV imports run in a transaction per chunk (e.g. 100 rows) with the `import_id` linking all `stock_movement` rows for auditability.

---

## 9. Concurrency & Oversell Protection

**The single most important rule:** every stock-mutating query holds a row lock.

```sql
-- inside a single transaction
SELECT stock FROM product_variant WHERE id = :variant_id FOR UPDATE;
-- ... business logic decides if we can decrement
UPDATE product_variant SET stock = stock - :qty WHERE id = :variant_id;
```

The `FOR UPDATE` serializes concurrent checkouts for the same variant. Postgres handles deadlock avoidance via the standard wait/retry; service catches `OperationalError` and returns 409 with a "please retry" hint.

For multi-item checkout, lock variants in a **deterministic order** (sorted by `variant_id`) to prevent classic deadlocks between two checkouts touching the same two variants in opposite orders.

---

## 10. Auditability

Every change to `product_variant.stock` MUST go through `inventory_service` and produce a `stock_movement` row. Direct DB updates that bypass the service are a bug — enforced by code review, not by triggers (Postgres triggers add hidden behaviour; the service layer keeps logic discoverable).

`stock_movement` is append-only:
- Never UPDATE existing rows.
- Never DELETE.
- Reasons are a closed enum: `order_placed`, `order_cancelled`, `manual_adjustment`, `csv_import`, `restock`.

---

## 11. Read-Side Caching

Stock is **never cached** in Phase 1. Every product detail page hit reads live `product_variant.stock`. Rationale:
- Read load at launch is low (200K social followers ≠ 200K simultaneous PDP visits).
- Stale stock causes oversell — the worst kind of customer-facing bug.
- If load forces caching later, use a short TTL (≤30s) + cache invalidation on stock change.

---

## 12. Reports the Admin Needs (Phase 1)

| Report | Endpoint | Source |
|---|---|---|
| Current stock per variant | `GET /admin/products/{id}/variants` | `product_variant` live |
| Low-stock list | `GET /admin/inventory/low-stock` | filtered query |
| Stock movement audit | `GET /admin/inventory/movements?variant_id=...&from=...&to=...` | `stock_movement` |
| Sales velocity (Phase 2) | `GET /admin/analytics/top-products` | join with `order_item` |

---

## 13. Open Inventory Questions

| Question | Default if unanswered |
|---|---|
| Should checkout reserve stock for 15 minutes between order create and payment success? | No — Razorpay path leaves stock untouched until capture (§4.1). Revisit if oversell occurs. |
| Pre-order / back-order support? | No (out of scope). Stock = 0 means unavailable; no waitlist. |
| Multi-warehouse? | No. |
| Bundle products (e.g. saree + blouse set)? | No in Phase 1. Each SKU stands alone. |
| Should low-stock threshold be product-level (one for all variants) instead of per-variant? | No — variant-level is more accurate; default 2 is a sensible global default. |
| What about COD orders that get refused at delivery — restock automatically? | Manual via admin cancel endpoint (§4.3). Auto-restock requires a delivery-confirmation webhook; not in scope. |
