from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TeacherOut(BaseModel):
    wp_user_id: int
    display_name: str
    avatar_url: str | None = None
    city: str = ""
    subjects: str = ""
    hourly_rate: int | None = None


class TeacherSubscribeIn(BaseModel):
    months: int = Field(default=3, ge=1, le=24)
    sessions_per_month: int = Field(default=8, ge=1, le=64)


class SubscriptionOut(BaseModel):
    id: int
    teacher_wp_id: int
    teacher_name: str
    student_wp_id: int
    student_name: str
    months: int
    sessions_per_month: int
    points_cost: int
    status: str
    starts_at: datetime
    ends_at: datetime | None = None


class StudentBalanceOut(BaseModel):
    student_wp_id: int
    balance: int


class TeacherStudentOut(BaseModel):
    wp_user_id: int
    display_name: str
    email: str
    status: str
    months: int
    sessions_per_month: int
    starts_at: datetime
    ends_at: datetime | None = None


class TeacherSessionCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    kind: str = Field(default="course")
    starts_at: datetime
    duration_minutes: int = Field(default=60, ge=15, le=600)
    meeting_url: str | None = None
    student_wp_user_id: int | None = None


class TeacherSessionOut(BaseModel):
    id: int
    teacher_wp_id: int
    student_wp_user_id: int | None = None
    title: str
    kind: str
    status: str
    starts_at: datetime
    duration_minutes: int
    meeting_url: str | None = None
    access_url: str | None = None


class SessionScheduleUpdateIn(BaseModel):
    starts_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=15, le=600)


class CalendarEventOut(BaseModel):
    id: str
    session_id: int
    type: str
    kind: str
    title: str
    teacher_wp_id: int
    student_wp_user_id: int | None = None
    teacher_name: str
    starts_at: datetime
    duration_minutes: int
    status: str


class LiveJoinOut(BaseModel):
    session_id: int
    join_url: str
    status: str
    kind: str


class SessionAccessOut(BaseModel):
    session_id: int
    access_url: str
    status: str
    kind: str


class SessionAccessTokenIn(BaseModel):
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class SessionAccessTokenOut(BaseModel):
    session_id: int
    token: str
    expires_at: datetime
    access_url: str


class SessionPresenceIn(BaseModel):
    event: str = Field(pattern="^(joined|left)$")


class SessionPresenceOut(BaseModel):
    session_id: int
    event: str
    event_at: datetime


class SessionPresenceUserOut(BaseModel):
    wp_user_id: int
    display_name: str
    role_tag: str | None = None
    avatar_url: str | None = None
    last_event_at: datetime


class SessionPresenceSnapshotOut(BaseModel):
    session_id: int
    online_count: int
    users: list[SessionPresenceUserOut] = Field(default_factory=list)


class PaymentCheckoutIn(BaseModel):
    teacher_wp_id: int
    months: int = Field(default=3, ge=1, le=24)
    sessions_per_month: int = Field(default=8, ge=1, le=64)
    provider: str = Field(default="auto")
    success_url: str | None = None
    cancel_url: str | None = None


class PaymentCheckoutOut(BaseModel):
    checkout_token: str
    checkout_url: str
    amount_cents: int
    currency: str
    provider: str
    status: str


class WalletTopupCheckoutIn(BaseModel):
    amount_cents: int = Field(ge=100, le=2000000)
    provider: str = Field(default="paypal")
    success_url: str | None = None
    cancel_url: str | None = None


class WalletTopupCheckoutOut(BaseModel):
    checkout_token: str
    checkout_url: str
    amount_cents: int
    currency: str
    provider: str
    status: str


class WalletTopupConfirmIn(BaseModel):
    checkout_token: str


class WalletTopupTransactionOut(BaseModel):
    id: int
    checkout_token: str
    amount_cents: int
    currency: str
    provider: str
    status: str
    provider_order_id: str | None = None
    provider_capture_id: str | None = None
    paid_at: datetime | None = None
    created_at: datetime


class WalletLedgerOut(BaseModel):
    id: int
    direction: str
    amount_cents: int
    points_delta: int
    source: str
    reference_type: str | None = None
    reference_id: str | None = None
    note: str | None = None
    created_at: datetime


class PaymentTransactionOut(BaseModel):
    id: int
    checkout_token: str
    amount_cents: int
    currency: str
    provider: str
    status: str
    provider_order_id: str | None = None
    provider_capture_id: str | None = None
    teacher_earnings_cents: int = 0
    platform_fee_cents: int = 0
    paid_at: datetime | None = None
    created_at: datetime


class TeacherEarningsOut(BaseModel):
    teacher_wp_id: int
    currency: str
    gross_cents: int
    earnings_cents: int
    platform_fee_cents: int
    paid_transactions: int


class PlatformRevenueOut(BaseModel):
    currency: str
    gross_cents: int
    teacher_earnings_cents: int
    platform_fee_cents: int
    paid_transactions: int


class TeacherWalletOut(BaseModel):
    teacher_wp_id: int
    currency: str
    total_earned_cents: int
    total_withdrawn_cents: int
    pending_withdrawals_cents: int
    available_cents: int


class TeacherWalletLedgerOut(BaseModel):
    id: int
    direction: str
    amount_cents: int
    source: str
    reference_type: str | None = None
    reference_id: str | None = None
    note: str | None = None
    created_at: datetime


class TeacherWithdrawCreateIn(BaseModel):
    amount_cents: int = Field(ge=100, le=100000000)
    method: str = Field(default="paypal")
    paypal_email: str | None = None
    note: str | None = Field(default=None, max_length=6000)


class TeacherWithdrawUpdateIn(BaseModel):
    status: str = Field(pattern="^(pending|processing|paid|rejected|cancelled)$")
    admin_note: str | None = Field(default=None, max_length=6000)
    external_ref: str | None = Field(default=None, max_length=120)


class TeacherWithdrawOut(BaseModel):
    id: int
    teacher_wp_id: int
    amount_cents: int
    currency: str
    method: str
    paypal_email: str | None = None
    status: str
    note: str | None = None
    admin_note: str | None = None
    external_ref: str | None = None
    processed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class StudentMoneyOut(BaseModel):
    student_wp_id: int
    currency: str
    deposited_cents: int
    spent_cents: int
    refunded_cents: int
    paid_transactions: int
    points_balance: int
