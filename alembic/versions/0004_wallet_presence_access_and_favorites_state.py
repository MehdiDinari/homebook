"""wallet ledger, session access/presence, favorites state

Revision ID: 0004_wallet_presence_access
Revises: 0003_paypal_money
Create Date: 2026-02-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_wallet_presence_access"
down_revision = "0003_paypal_money"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("book_favorites") as batch_op:
        batch_op.add_column(
            sa.Column(
                "state",
                sa.String(length=20),
                nullable=False,
                server_default=sa.text("'favorite'"),
            )
        )
        batch_op.create_check_constraint(
            "ck_book_favorite_state",
            "state in ('favorite','to_read')",
        )

    op.create_table(
        "wallet_topup_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_user_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("checkout_token", sa.String(length=120), nullable=False),
        sa.Column("checkout_url", sa.String(length=1200), nullable=False),
        sa.Column("provider_order_id", sa.String(length=120), nullable=True),
        sa.Column("provider_capture_id", sa.String(length=120), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_wallet_topup_amount_positive"),
        sa.ForeignKeyConstraint(["student_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_token", name="uq_wallet_topup_checkout_token"),
    )
    op.create_index(
        "ix_wallet_topup_transactions_student_user_id",
        "wallet_topup_transactions",
        ["student_user_id"],
        unique=False,
    )

    op.create_table(
        "wallet_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_user_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=12), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("points_delta", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=True),
        sa.Column("reference_id", sa.String(length=120), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents >= 0", name="ck_wallet_ledger_amount_non_negative"),
        sa.CheckConstraint("direction in ('credit','debit')", name="ck_wallet_ledger_direction"),
        sa.ForeignKeyConstraint(["student_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wallet_ledger_student_user_id", "wallet_ledger", ["student_user_id"], unique=False)

    op.create_table(
        "session_access_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=120), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["teacher_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token", name="uq_session_access_token"),
    )
    op.create_index("ix_session_access_tokens_session_id", "session_access_tokens", ["session_id"], unique=False)
    op.create_index(
        "ix_session_access_tokens_created_by_user_id",
        "session_access_tokens",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "session_presence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=20), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event in ('joined','left')", name="ck_session_presence_event"),
        sa.ForeignKeyConstraint(["session_id"], ["teacher_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_session_presence_session_id", "session_presence", ["session_id"], unique=False)
    op.create_index("ix_session_presence_user_id", "session_presence", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_session_presence_user_id", table_name="session_presence")
    op.drop_index("ix_session_presence_session_id", table_name="session_presence")
    op.drop_table("session_presence")

    op.drop_index("ix_session_access_tokens_created_by_user_id", table_name="session_access_tokens")
    op.drop_index("ix_session_access_tokens_session_id", table_name="session_access_tokens")
    op.drop_table("session_access_tokens")

    op.drop_index("ix_wallet_ledger_student_user_id", table_name="wallet_ledger")
    op.drop_table("wallet_ledger")

    op.drop_index("ix_wallet_topup_transactions_student_user_id", table_name="wallet_topup_transactions")
    op.drop_table("wallet_topup_transactions")

    with op.batch_alter_table("book_favorites") as batch_op:
        batch_op.drop_constraint("ck_book_favorite_state", type_="check")
        batch_op.drop_column("state")
