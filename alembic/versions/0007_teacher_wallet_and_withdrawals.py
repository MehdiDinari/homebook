"""teacher wallet ledger and withdrawal requests

Revision ID: 0007_teacher_wallet_wd
Revises: 0006_support_tickets
Create Date: 2026-02-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_teacher_wallet_wd"
down_revision = "0006_support_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_wallet_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("teacher_user_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=12), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=True),
        sa.Column("reference_id", sa.String(length=120), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents >= 0", name="ck_teacher_wallet_amount_non_negative"),
        sa.CheckConstraint("direction in ('credit','debit')", name="ck_teacher_wallet_direction"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teacher_wallet_ledger_teacher_user_id", "teacher_wallet_ledger", ["teacher_user_id"], unique=False)

    op.create_table(
        "teacher_withdrawal_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("teacher_user_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("paypal_email", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_teacher_withdraw_amount_positive"),
        sa.CheckConstraint(
            "status in ('pending','processing','paid','rejected','cancelled')",
            name="ck_teacher_withdraw_status",
        ),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_teacher_withdrawal_requests_teacher_user_id",
        "teacher_withdrawal_requests",
        ["teacher_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_teacher_withdrawal_requests_teacher_user_id", table_name="teacher_withdrawal_requests")
    op.drop_table("teacher_withdrawal_requests")

    op.drop_index("ix_teacher_wallet_ledger_teacher_user_id", table_name="teacher_wallet_ledger")
    op.drop_table("teacher_wallet_ledger")
