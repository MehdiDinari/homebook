"""education scheduler and payments tables

Revision ID: 0002_edu_sched_payments
Revises: 0001_initial
Create Date: 2026-02-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_edu_sched_payments"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("subjects", sa.String(length=500), nullable=True),
        sa.Column("hourly_rate", sa.Integer(), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_teacher_profiles_user_id", "teacher_profiles", ["user_id"], unique=False)

    op.create_table(
        "student_balances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_student_balances_user_id", "student_balances", ["user_id"], unique=False)

    op.create_table(
        "teacher_student_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("teacher_user_id", sa.Integer(), nullable=False),
        sa.Column("student_user_id", sa.Integer(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("sessions_per_month", sa.Integer(), nullable=False),
        sa.Column("points_cost", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("months > 0", name="ck_subscription_months_positive"),
        sa.CheckConstraint("sessions_per_month > 0", name="ck_subscription_sessions_positive"),
        sa.CheckConstraint("points_cost >= 0", name="ck_subscription_points_non_negative"),
        sa.CheckConstraint("teacher_user_id <> student_user_id", name="ck_subscription_teacher_student_diff"),
        sa.ForeignKeyConstraint(["student_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("teacher_user_id", "student_user_id", "status", name="uq_teacher_student_status"),
    )
    op.create_index(
        "ix_teacher_student_subscriptions_teacher_user_id",
        "teacher_student_subscriptions",
        ["teacher_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_teacher_student_subscriptions_student_user_id",
        "teacher_student_subscriptions",
        ["student_user_id"],
        unique=False,
    )

    op.create_table(
        "teacher_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("teacher_user_id", sa.Integer(), nullable=False),
        sa.Column("target_student_user_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("meeting_url", sa.String(length=1200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("duration_minutes > 0", name="ck_teacher_session_duration_positive"),
        sa.ForeignKeyConstraint(["target_student_user_id"], ["users_shadow.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_teacher_sessions_teacher_user_id", "teacher_sessions", ["teacher_user_id"], unique=False)
    op.create_index(
        "ix_teacher_sessions_target_student_user_id",
        "teacher_sessions",
        ["target_student_user_id"],
        unique=False,
    )

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_user_id", sa.Integer(), nullable=False),
        sa.Column("teacher_user_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("sessions_per_month", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("checkout_token", sa.String(length=120), nullable=False),
        sa.Column("checkout_url", sa.String(length=1200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payment_amount_non_negative"),
        sa.ForeignKeyConstraint(["student_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["teacher_student_subscriptions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["teacher_user_id"], ["users_shadow.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checkout_token", name="uq_payment_checkout_token"),
    )
    op.create_index("ix_payment_transactions_student_user_id", "payment_transactions", ["student_user_id"], unique=False)
    op.create_index("ix_payment_transactions_teacher_user_id", "payment_transactions", ["teacher_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payment_transactions_teacher_user_id", table_name="payment_transactions")
    op.drop_index("ix_payment_transactions_student_user_id", table_name="payment_transactions")
    op.drop_table("payment_transactions")

    op.drop_index("ix_teacher_sessions_target_student_user_id", table_name="teacher_sessions")
    op.drop_index("ix_teacher_sessions_teacher_user_id", table_name="teacher_sessions")
    op.drop_table("teacher_sessions")

    op.drop_index("ix_teacher_student_subscriptions_student_user_id", table_name="teacher_student_subscriptions")
    op.drop_index("ix_teacher_student_subscriptions_teacher_user_id", table_name="teacher_student_subscriptions")
    op.drop_table("teacher_student_subscriptions")

    op.drop_index("ix_student_balances_user_id", table_name="student_balances")
    op.drop_table("student_balances")

    op.drop_index("ix_teacher_profiles_user_id", table_name="teacher_profiles")
    op.drop_table("teacher_profiles")
