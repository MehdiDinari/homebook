from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin, utcnow


class TeacherProfile(TimestampMixin, Base):
    __tablename__ = "teacher_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), unique=True, index=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subjects: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hourly_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class StudentBalance(TimestampMixin, Base):
    __tablename__ = "student_balances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), unique=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, default=500, nullable=False)


class TeacherStudentSubscription(TimestampMixin, Base):
    __tablename__ = "teacher_student_subscriptions"
    __table_args__ = (
        UniqueConstraint("teacher_user_id", "student_user_id", "status", name="uq_teacher_student_status"),
        CheckConstraint("months > 0", name="ck_subscription_months_positive"),
        CheckConstraint("sessions_per_month > 0", name="ck_subscription_sessions_positive"),
        CheckConstraint("points_cost >= 0", name="ck_subscription_points_non_negative"),
        CheckConstraint("teacher_user_id <> student_user_id", name="ck_subscription_teacher_student_diff"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    sessions_per_month: Mapped[int] = mapped_column(Integer, nullable=False)
    points_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeacherSession(TimestampMixin, Base):
    __tablename__ = "teacher_sessions"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_teacher_session_duration_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    target_student_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users_shadow.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="course", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="scheduled", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    meeting_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)


class PaymentTransaction(TimestampMixin, Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint("checkout_token", name="uq_payment_checkout_token"),
        CheckConstraint("amount_cents >= 0", name="ck_payment_amount_non_negative"),
        CheckConstraint("teacher_earnings_cents >= 0", name="ck_payment_teacher_earnings_non_negative"),
        CheckConstraint("platform_fee_cents >= 0", name="ck_payment_platform_fee_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("teacher_student_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )
    months: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    sessions_per_month: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="mock", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    checkout_token: Mapped[str] = mapped_column(String(120), nullable=False)
    checkout_url: Mapped[str] = mapped_column(String(1200), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_capture_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    teacher_earnings_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    platform_fee_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WalletTopupTransaction(TimestampMixin, Base):
    __tablename__ = "wallet_topup_transactions"
    __table_args__ = (
        UniqueConstraint("checkout_token", name="uq_wallet_topup_checkout_token"),
        CheckConstraint("amount_cents > 0", name="ck_wallet_topup_amount_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="paypal", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    checkout_token: Mapped[str] = mapped_column(String(120), nullable=False)
    checkout_url: Mapped[str] = mapped_column(String(1200), nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_capture_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WalletLedger(TimestampMixin, Base):
    __tablename__ = "wallet_ledger"
    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_wallet_ledger_amount_non_negative"),
        CheckConstraint("direction in ('credit','debit')", name="ck_wallet_ledger_direction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    points_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TeacherWalletLedger(TimestampMixin, Base):
    __tablename__ = "teacher_wallet_ledger"
    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="ck_teacher_wallet_amount_non_negative"),
        CheckConstraint("direction in ('credit','debit')", name="ck_teacher_wallet_direction"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TeacherWithdrawalRequest(TimestampMixin, Base):
    __tablename__ = "teacher_withdrawal_requests"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_teacher_withdraw_amount_positive"),
        CheckConstraint(
            "status in ('pending','processing','paid','rejected','cancelled')",
            name="ck_teacher_withdraw_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    method: Mapped[str] = mapped_column(String(20), default="paypal", nullable=False)
    paypal_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionAccessToken(TimestampMixin, Base):
    __tablename__ = "session_access_tokens"
    __table_args__ = (
        UniqueConstraint("token", name="uq_session_access_token"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("teacher_sessions.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionPresence(TimestampMixin, Base):
    __tablename__ = "session_presence"
    __table_args__ = (
        CheckConstraint("event in ('joined','left')", name="ck_session_presence_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("teacher_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(20), nullable=False)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
