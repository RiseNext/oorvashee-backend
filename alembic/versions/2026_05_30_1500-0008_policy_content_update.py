"""Update store-policy CONTENT to current business rules.

DATA-ONLY migration — no schema changes. Updates the title/summary/body of the
seeded policy rows (shipping, refund, terms, privacy) to reflect the current
policies: domestic-only shipping, all-sales-final / no returns-exchanges-refunds,
prepaid-only payments, and the Oorvashee Saree House brand name. Keyed by the
stable `key`, so it is idempotent and safe to run hot.

Admins can still edit this copy afterwards from the dashboard; this migration
just brings existing + fresh databases to the correct baseline content.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Paragraphs joined with a blank line — the storefront splits on blank lines.
_CONTENT = {
    "shipping": {
        "title": "Shipping Policy",
        "summary": (
            "Domestic delivery across India only — metro orders in 1–2 days, "
            "other locations vary; tracking shared after dispatch."
        ),
        "body": (
            "We currently ship across India only; international delivery is not available. "
            "Customers outside India may place orders using international payment methods, "
            "provided the delivery address is within India.\n\n"
            "Orders are generally processed and dispatched within one to three business days "
            "after successful payment confirmation. Metro cities are usually delivered within "
            "1–2 business days, while delivery timelines for other locations across India may "
            "vary depending on the destination, courier network, weather, and public holidays.\n\n"
            "Customers receive shipment tracking information once their order is dispatched. "
            "While we work with trusted logistics partners, occasional delays beyond our control "
            "may arise; our support team remains available on WhatsApp to assist with any "
            "shipment-related concerns."
        ),
    },
    "refund": {
        "title": "Returns & Refunds",
        "summary": "All sales are final — no returns, exchanges, or refunds are accepted.",
        "body": (
            "All sales are final. No returns, exchanges, or refunds are accepted once an order "
            "has been placed.\n\n"
            "We encourage customers to review product details, descriptions, measurements, and "
            "images carefully before completing a purchase. If you have any questions about a "
            "product, please contact us on WhatsApp before ordering — our team will gladly help "
            "you choose with confidence."
        ),
    },
    "terms": {
        "title": "Terms & Conditions",
        "summary": (
            "The terms you agree to when shopping with Oorvashee Saree House — payments, "
            "orders, and intellectual property."
        ),
        "body": (
            "By accessing and using the Oorvashee Saree House website, customers agree to comply "
            "with all applicable terms, policies, and legal requirements. The website content, "
            "products, pricing, and services are subject to change without prior notice.\n\n"
            "All orders are prepaid — we accept online payments only, and Cash on Delivery is not "
            "available. All sales are final, with no returns, exchanges, or refunds. Orders are "
            "subject to product availability and verification, and Oorvashee Saree House reserves "
            "the right to cancel or refuse orders involving pricing errors, stock issues, "
            "fraudulent activity, or policy violations.\n\n"
            "All intellectual property, including logos, designs, product images, content, and "
            "branding elements, remains the exclusive property of Oorvashee Saree House. "
            "Unauthorized reproduction, distribution, or commercial use of website content is "
            "strictly prohibited."
        ),
    },
    "privacy": {
        "title": "Privacy Policy",
        "summary": (
            "How we collect, use, and safeguard your personal information — and our promise "
            "never to sell it."
        ),
        "body": (
            "Oorvashee Saree House respects the privacy of its customers and is committed to "
            "protecting personal information. We collect information such as names, contact "
            "details, addresses, and order-related information solely for business operations "
            "and customer service purposes.\n\n"
            "The information collected helps us process orders, provide customer support, improve "
            "our services, communicate order updates, and deliver a personalized shopping "
            "experience. We implement appropriate security measures to safeguard customer data "
            "against unauthorized access or misuse.\n\n"
            "Oorvashee Saree House does not sell customer information to third parties. Certain "
            "trusted service providers, such as payment gateways, logistics partners, and "
            "analytics platforms, may access limited information required to perform their "
            "services. By using our website, customers consent to the practices outlined in this "
            "policy."
        ),
    },
}

_policies = sa.table(
    "policies",
    sa.column("key", sa.String),
    sa.column("title", sa.String),
    sa.column("summary", sa.String),
    sa.column("body", sa.Text),
)


def upgrade() -> None:
    bind = op.get_bind()
    for key, vals in _CONTENT.items():
        bind.execute(
            sa.update(_policies).where(_policies.c.key == key).values(**vals)
        )


def downgrade() -> None:
    # Content-only migration; previous copy is not restored on downgrade.
    pass
