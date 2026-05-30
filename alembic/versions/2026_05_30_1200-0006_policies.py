"""Store policies table + seed the fixed doc set.

Additive only. Creates the `policies` table and seeds the four store-wide
documents (shipping, refund, terms, privacy) so the storefront has content the
moment the migration lands. Admins edit these rows from the dashboard
thereafter; the seed runs only on first create (guarded by the unique `key`).

Safe to run hot.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Canonical default copy. Paragraphs joined with a blank line — the storefront
# splits on blank lines for display. Kept here (not in app code) so a fresh DB
# is populated purely by migrating.
_DEFAULTS = [
    {
        "key": "shipping",
        "title": "Shipping Policy",
        "summary": "Order processing times, delivery timelines, and how tracking is shared after dispatch.",
        "body": (
            "Oorvashee aims to process and dispatch orders efficiently to ensure a smooth shopping experience. "
            "Orders are generally processed within one to three business days after successful payment confirmation.\n\n"
            "Delivery timelines vary depending on customer location, courier network availability, weather conditions, "
            "and public holidays. Most domestic orders are delivered within a few business days, although remote "
            "locations may require additional transit time.\n\n"
            "Customers receive shipment tracking information once orders are dispatched. While Oorvashee works with "
            "trusted logistics partners, unforeseen delays beyond our control may occasionally occur. Our support team "
            "remains available to assist customers with shipment-related concerns."
        ),
        "display_order": 1,
    },
    {
        "key": "refund",
        "title": "Refund Policy",
        "summary": "When refunds apply, how requests are reviewed, and how approved refunds are processed.",
        "body": (
            "Customer satisfaction is important to Oorvashee. Refund requests may be considered for products that "
            "arrive damaged, defective, incorrect, or significantly different from their description. Customers should "
            "report such issues within the specified return window.\n\n"
            "All refund requests undergo a review process that may include verification of photographs, videos, order "
            "details, and product condition. Once approved, refunds are initiated through the original payment method "
            "used during purchase.\n\n"
            "Refund processing times may vary depending on payment providers and banking institutions. Products that "
            "show signs of use, damage caused after delivery, or requests submitted outside the permitted period may "
            "not qualify for refunds."
        ),
        "display_order": 2,
    },
    {
        "key": "terms",
        "title": "Terms & Conditions",
        "summary": "The terms you agree to when using Oorvashee, including orders, pricing, and intellectual property.",
        "body": (
            "By accessing and using the Oorvashee website, customers agree to comply with all applicable terms, "
            "policies, and legal requirements. The website content, products, pricing, and services are subject to "
            "change without prior notice.\n\n"
            "All orders placed through our platform are subject to product availability and verification. Oorvashee "
            "reserves the right to cancel or refuse orders in cases involving pricing errors, stock issues, fraudulent "
            "activity, or policy violations.\n\n"
            "All intellectual property, including logos, designs, product images, content, and branding elements, "
            "remains the exclusive property of Oorvashee. Unauthorized reproduction, distribution, or commercial use "
            "of website content is strictly prohibited."
        ),
        "display_order": 3,
    },
    {
        "key": "privacy",
        "title": "Privacy Policy",
        "summary": "How we collect, use, and safeguard your personal information — and our promise never to sell it.",
        "body": (
            "Oorvashee respects the privacy of its customers and is committed to protecting personal information. We "
            "collect information such as names, contact details, addresses, and order-related information solely for "
            "business operations and customer service purposes.\n\n"
            "The information collected helps us process orders, provide customer support, improve our services, "
            "communicate order updates, and deliver a personalized shopping experience. We implement appropriate "
            "security measures to safeguard customer data against unauthorized access or misuse.\n\n"
            "Oorvashee does not sell customer information to third parties. Certain trusted service providers, such as "
            "payment gateways, logistics partners, and analytics platforms, may access limited information required to "
            "perform their services. By using our website, customers consent to the practices outlined in this policy."
        ),
        "display_order": 4,
    },
]


def upgrade() -> None:
    policies = op.create_table(
        "policies",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("summary", sa.String(400), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "display_order", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint("key", name="uq_policies_key"),
    )

    # Seed the fixed doc set. server_default fills id/timestamps/is_active.
    op.bulk_insert(policies, _DEFAULTS)


def downgrade() -> None:
    op.drop_table("policies")
