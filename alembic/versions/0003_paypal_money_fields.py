"""paypal + money tracking fields

Revision ID: 0003_paypal_money
Revises: 0002_edu_sched_payments
Create Date: 2026-02-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_paypal_money"
down_revision = "0002_edu_sched_payments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_transactions", sa.Column("provider_order_id", sa.String(length=120), nullable=True))
    op.add_column("payment_transactions", sa.Column("provider_capture_id", sa.String(length=120), nullable=True))
    op.add_column("payment_transactions", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "payment_transactions",
        sa.Column("teacher_earnings_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "payment_transactions",
        sa.Column("platform_fee_cents", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        "ck_payment_teacher_earnings_non_negative",
        "payment_transactions",
        "teacher_earnings_cents >= 0",
    )
    op.create_check_constraint(
        "ck_payment_platform_fee_non_negative",
        "payment_transactions",
        "platform_fee_cents >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_payment_platform_fee_non_negative", "payment_transactions", type_="check")
    op.drop_constraint("ck_payment_teacher_earnings_non_negative", "payment_transactions", type_="check")
    op.drop_column("payment_transactions", "platform_fee_cents")
    op.drop_column("payment_transactions", "teacher_earnings_cents")
    op.drop_column("payment_transactions", "paid_at")
    op.drop_column("payment_transactions", "provider_capture_id")
    op.drop_column("payment_transactions", "provider_order_id")
