from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, case, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.education import (
    PaymentTransaction,
    SessionAccessToken,
    SessionPresence,
    StudentBalance,
    TeacherProfile,
    TeacherSession,
    TeacherStudentSubscription,
    TeacherWalletLedger,
    TeacherWithdrawalRequest,
    WalletLedger,
    WalletTopupTransaction,
)
from app.models.user import Profile, UserShadow
from app.schemas.education import (
    CalendarEventOut,
    LiveJoinOut,
    PaymentCheckoutIn,
    PaymentCheckoutOut,
    PaymentTransactionOut,
    PlatformRevenueOut,
    SessionAccessOut,
    SessionAccessTokenIn,
    SessionAccessTokenOut,
    SessionPresenceIn,
    SessionPresenceOut,
    SessionPresenceSnapshotOut,
    SessionPresenceUserOut,
    SessionScheduleUpdateIn,
    StudentMoneyOut,
    StudentBalanceOut,
    SubscriptionOut,
    TeacherEarningsOut,
    TeacherOut,
    TeacherWalletLedgerOut,
    TeacherWalletOut,
    TeacherSessionCreateIn,
    TeacherSessionOut,
    TeacherStudentOut,
    TeacherSubscribeIn,
    TeacherWithdrawCreateIn,
    TeacherWithdrawOut,
    TeacherWithdrawUpdateIn,
    WalletTopupConfirmIn,
    WalletTopupCheckoutIn,
    WalletTopupCheckoutOut,
    WalletTopupTransactionOut,
    WalletLedgerOut,
)
from app.services.payments import (
    PaymentProviderError,
    capture_paypal_order,
    create_paypal_payout,
    create_paypal_order,
    create_stripe_checkout_session,
    get_stripe_checkout_session,
)
from app.services.ws import ws_manager
from app.services.wordpress import fetch_wp_user_by_email, fetch_wp_user_by_id, fetch_wp_users_by_role

router = APIRouter(tags=["education"])
logger = logging.getLogger(__name__)

_BASE_TEACHER_ROLE_ALIASES = {"teacher", "instructor", "administrator", "prof"}
_BASE_STUDENT_ROLE_ALIASES = {"student"}
SESSION_PRESENCE_STALE_SECONDS = 120
_WITHDRAW_ALLOWED_STATUSES = {"pending", "processing", "paid", "rejected", "cancelled"}
_WITHDRAW_ALLOWED_METHODS = {"paypal", "manual", "bank"}


def _teacher_role_aliases() -> set[str]:
    out = set(_BASE_TEACHER_ROLE_ALIASES)
    if settings.wp_role_teacher:
        out.add(settings.wp_role_teacher.strip().lower())
    return out


def _student_role_aliases() -> set[str]:
    out = set(_BASE_STUDENT_ROLE_ALIASES)
    if settings.wp_role_student:
        out.add(settings.wp_role_student.strip().lower())
    return out


def _parse_roles(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def _role_match(roles: list[str], aliases: set[str]) -> bool:
    return bool(set(roles).intersection(aliases))


def _role_tag_from_roles(roles: list[str] | None) -> str | None:
    parsed = {str(x).strip().lower() for x in (roles or []) if str(x).strip()}
    if "administrator" in parsed:
        return "admin"
    if parsed.intersection({"prof", "teacher", "instructor"}):
        return "prof"
    if "student" in parsed:
        return "student"
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _calc_points_cost(months: int, sessions_per_month: int) -> int:
    return int(months * sessions_per_month * 5)


def _calc_teacher_earnings_cents(amount_cents: int) -> tuple[int, int]:
    # Business rule: teacher always gets 70%, platform(admin) always keeps 30%.
    earnings = int(round(int(amount_cents) * 0.7))
    fee = max(int(amount_cents) - earnings, 0)
    return earnings, fee


def _default_payment_success_url() -> str:
    if settings.paypal_success_url:
        return settings.paypal_success_url.strip()
    if settings.stripe_success_url:
        return settings.stripe_success_url.strip()
    if settings.wp_base_url:
        return f"{settings.wp_base_url.rstrip('/')}/paiement-ok/"
    return "https://example.com/paiement-ok/"


def _default_payment_cancel_url() -> str:
    if settings.paypal_cancel_url:
        return settings.paypal_cancel_url.strip()
    if settings.stripe_cancel_url:
        return settings.stripe_cancel_url.strip()
    if settings.wp_base_url:
        return f"{settings.wp_base_url.rstrip('/')}/paiement-annule/"
    return "https://example.com/paiement-annule/"


def _session_fallback_url(*, session_id: int, kind: str) -> str:
    base = settings.wp_base_url.rstrip("/") if settings.wp_base_url else ""
    # Keep course slug unchanged, but use new live slug.
    room_path = "live" if kind == "live" else "course-room"
    if base:
        return f"{base}/{room_path}/?session_id={session_id}"
    return f"/{room_path}/?session_id={session_id}"


def _default_meeting_url(kind: str) -> str:
    # Avoid predictable room names that can be re-used/locked by external participants.
    room_prefix = "homebook-live" if str(kind).strip().lower() == "live" else "homebook-course"
    room_name = f"{room_prefix}-{uuid.uuid4().hex[:14]}"
    return f"https://meet.jit.si/{room_name}"


async def _ensure_session_meeting_url(db: AsyncSession, row: TeacherSession) -> str:
    current = str(row.meeting_url or "").strip()
    if current:
        return current
    row.meeting_url = _default_meeting_url(row.kind)
    await db.commit()
    await db.refresh(row)
    return str(row.meeting_url or "")


def _session_access_url(row: TeacherSession) -> str:
    if row.meeting_url:
        return row.meeting_url
    return _session_fallback_url(session_id=row.id, kind=row.kind)


def _append_query(url: str, key: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[key] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _points_from_cents(amount_cents: int) -> int:
    return max(int(amount_cents // 100), 0)


async def _record_wallet_ledger(
    db: AsyncSession,
    *,
    student_user_id: int,
    direction: str,
    amount_cents: int,
    points_delta: int,
    source: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    note: str | None = None,
) -> WalletLedger:
    # Idempotence guard for repeated confirmations/callback retries.
    if reference_type and reference_id:
        existing = (
            await db.execute(
                select(WalletLedger).where(
                    and_(
                        WalletLedger.student_user_id == student_user_id,
                        WalletLedger.direction == direction,
                        WalletLedger.source == source,
                        WalletLedger.reference_type == reference_type,
                        WalletLedger.reference_id == reference_id,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    row = WalletLedger(
        student_user_id=student_user_id,
        direction=direction,
        amount_cents=max(int(amount_cents), 0),
        points_delta=int(points_delta),
        source=source,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
    )
    db.add(row)
    return row


async def _record_teacher_wallet_ledger(
    db: AsyncSession,
    *,
    teacher_user_id: int,
    direction: str,
    amount_cents: int,
    source: str,
    reference_type: str | None = None,
    reference_id: str | None = None,
    note: str | None = None,
) -> TeacherWalletLedger:
    if reference_type and reference_id:
        existing = (
            await db.execute(
                select(TeacherWalletLedger).where(
                    and_(
                        TeacherWalletLedger.teacher_user_id == teacher_user_id,
                        TeacherWalletLedger.direction == direction,
                        TeacherWalletLedger.source == source,
                        TeacherWalletLedger.reference_type == reference_type,
                        TeacherWalletLedger.reference_id == reference_id,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    row = TeacherWalletLedger(
        teacher_user_id=teacher_user_id,
        direction=direction,
        amount_cents=max(int(amount_cents), 0),
        source=source,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note,
    )
    db.add(row)
    return row


async def _teacher_wallet_net_cents(db: AsyncSession, teacher_user_id: int) -> int:
    stmt = select(
        func.coalesce(
            func.sum(
                case(
                    (TeacherWalletLedger.direction == "credit", TeacherWalletLedger.amount_cents),
                    else_=-TeacherWalletLedger.amount_cents,
                )
            ),
            0,
        )
    ).where(TeacherWalletLedger.teacher_user_id == teacher_user_id)
    value = (await db.execute(stmt)).scalar_one()
    return int(value or 0)


async def _teacher_pending_withdrawals_cents(db: AsyncSession, teacher_user_id: int) -> int:
    stmt = select(func.coalesce(func.sum(TeacherWithdrawalRequest.amount_cents), 0)).where(
        and_(
            TeacherWithdrawalRequest.teacher_user_id == teacher_user_id,
            TeacherWithdrawalRequest.status.in_(["pending", "processing"]),
        )
    )
    value = (await db.execute(stmt)).scalar_one()
    return int(value or 0)


def _to_teacher_withdraw_out(row: TeacherWithdrawalRequest, teacher: UserShadow) -> TeacherWithdrawOut:
    return TeacherWithdrawOut(
        id=row.id,
        teacher_wp_id=teacher.wp_user_id,
        amount_cents=row.amount_cents,
        currency=row.currency,
        method=row.method,
        paypal_email=row.paypal_email,
        status=row.status,
        note=row.note,
        admin_note=row.admin_note,
        external_ref=row.external_ref,
        processed_at=row.processed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _credit_teacher_wallet_from_payment(
    db: AsyncSession,
    *,
    payment: PaymentTransaction,
    teacher_wp_user_id: int,
) -> None:
    earnings = max(int(payment.teacher_earnings_cents or 0), 0)
    if earnings <= 0:
        return
    await _record_teacher_wallet_ledger(
        db,
        teacher_user_id=payment.teacher_user_id,
        direction="credit",
        amount_cents=earnings,
        source="course_payment",
        reference_type="payment_transaction",
        reference_id=str(payment.id),
        note=f"Teacher earning from student payment (teacher_wp_id={teacher_wp_user_id})",
    )


async def _sync_teacher_wallet_from_paid_transactions(
    db: AsyncSession,
    *,
    teacher: UserShadow,
) -> None:
    paid_rows = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                and_(
                    PaymentTransaction.teacher_user_id == teacher.id,
                    PaymentTransaction.status == "paid",
                )
            )
            .order_by(PaymentTransaction.id.asc())
        )
    ).scalars().all()
    if not paid_rows:
        return
    for row in paid_rows:
        if int(row.teacher_earnings_cents or 0) <= 0 and int(row.amount_cents or 0) > 0:
            row.teacher_earnings_cents, row.platform_fee_cents = _calc_teacher_earnings_cents(row.amount_cents)
        await _credit_teacher_wallet_from_payment(
            db,
            payment=row,
            teacher_wp_user_id=teacher.wp_user_id,
        )
    await db.commit()


async def _upsert_user_shadow(
    db: AsyncSession,
    *,
    wp_user_id: int,
    email: str,
    display_name: str,
    roles: list[str],
    avatar_url: str | None = None,
) -> UserShadow:
    row = (await db.execute(select(UserShadow).where(UserShadow.wp_user_id == wp_user_id))).scalar_one_or_none()
    clean_roles = [str(x).strip().lower() for x in roles if str(x).strip()]
    clean_email = (email or "").strip().lower()
    clean_name = (display_name or "").strip()
    clean_avatar = (avatar_url or "").strip()
    if clean_avatar and not clean_avatar.lower().startswith(("http://", "https://")):
        clean_avatar = ""
    if not clean_email:
        raise HTTPException(status_code=401, detail="WordPress user email missing")
    if not clean_name:
        clean_name = clean_email
    if row is None:
        row = UserShadow(
            wp_user_id=wp_user_id,
            email=clean_email,
            display_name=clean_name,
            roles=clean_roles,
        )
        db.add(row)
        await db.flush()
    else:
        row.email = clean_email
        row.display_name = clean_name
        if clean_roles:
            row.roles = clean_roles

    if clean_avatar:
        profile = (await db.execute(select(Profile).where(Profile.user_id == row.id))).scalar_one_or_none()
        if profile is None:
            db.add(Profile(user_id=row.id, avatar_url=clean_avatar))
        elif profile.avatar_url != clean_avatar:
            profile.avatar_url = clean_avatar

    await db.commit()
    await db.refresh(row)
    return row


async def _sync_from_wp(db: AsyncSession, wp_user_id: int) -> UserShadow:
    try:
        wp_user = await fetch_wp_user_by_id(wp_user_id)
    except Exception as exc:
        logger.exception("WordPress fetch failed for user id=%s", wp_user_id)
        raise HTTPException(status_code=502, detail="WordPress API unavailable") from exc
    if wp_user is None:
        raise HTTPException(status_code=404, detail="WordPress user not found")
    return await _upsert_user_shadow(
        db,
        wp_user_id=wp_user["id"],
        email=wp_user.get("email", ""),
        display_name=wp_user.get("display_name", ""),
        roles=wp_user.get("roles", []),
        avatar_url=str(wp_user.get("avatar_url") or "").strip() or None,
    )


async def _resolve_actor(
    db: AsyncSession,
    *,
    x_wp_user_id: int | None,
    x_user_email: str | None,
    x_wp_user_roles: str | None,
) -> UserShadow:
    header_roles = _parse_roles(x_wp_user_roles)
    resolved_wp_user_id = x_wp_user_id
    resolved_user: dict | None = None

    try:
        if resolved_wp_user_id is not None:
            resolved_user = await fetch_wp_user_by_id(int(resolved_wp_user_id))
        elif x_user_email:
            resolved_user = await fetch_wp_user_by_email((x_user_email or "").strip().lower())
        else:
            raise HTTPException(status_code=401, detail="Missing X-WP-User-Id or X-User-Email")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "WordPress identity lookup failed (wp_user_id=%s, email=%s)",
            resolved_wp_user_id,
            (x_user_email or "").strip().lower(),
        )
        raise HTTPException(status_code=502, detail="WordPress identity lookup failed") from exc

    if resolved_user is None:
        raise HTTPException(status_code=401, detail="Unable to resolve WordPress user")

    roles = [str(x).strip().lower() for x in (resolved_user.get("roles") or []) if str(x).strip()]
    for role in header_roles:
        if role not in roles:
            roles.append(role)
    return await _upsert_user_shadow(
        db,
        wp_user_id=int(resolved_user["id"]),
        email=str(resolved_user.get("email") or "").strip().lower(),
        display_name=str(resolved_user.get("display_name") or "").strip(),
        roles=roles,
        avatar_url=str(resolved_user.get("avatar_url") or "").strip() or None,
    )


async def get_request_user(
    db: AsyncSession = Depends(get_db),
    x_wp_user_id: int | None = Header(default=None, alias="X-WP-User-Id"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    x_wp_user_roles: str | None = Header(default=None, alias="X-WP-User-Roles"),
) -> UserShadow:
    return await _resolve_actor(
        db,
        x_wp_user_id=x_wp_user_id,
        x_user_email=x_user_email,
        x_wp_user_roles=x_wp_user_roles,
    )


async def get_request_user_optional(
    db: AsyncSession = Depends(get_db),
    x_wp_user_id: int | None = Header(default=None, alias="X-WP-User-Id"),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
    x_wp_user_roles: str | None = Header(default=None, alias="X-WP-User-Roles"),
) -> UserShadow | None:
    if x_wp_user_id is None and not x_user_email:
        return None
    return await _resolve_actor(
        db,
        x_wp_user_id=x_wp_user_id,
        x_user_email=x_user_email,
        x_wp_user_roles=x_wp_user_roles,
    )


def _is_admin(user: UserShadow) -> bool:
    return "administrator" in [str(x).lower() for x in (user.roles or [])]


def _is_teacher(user: UserShadow) -> bool:
    return _role_match([str(x).lower() for x in (user.roles or [])], _teacher_role_aliases())


def _is_student(user: UserShadow) -> bool:
    return _role_match([str(x).lower() for x in (user.roles or [])], _student_role_aliases())


def _ensure_self_or_admin(actor: UserShadow, target_wp_user_id: int) -> None:
    if actor.wp_user_id == target_wp_user_id or _is_admin(actor):
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def _ensure_teacher_owner_or_admin(actor: UserShadow, teacher_wp_user_id: int) -> None:
    if _is_admin(actor):
        return
    if actor.wp_user_id == teacher_wp_user_id and _is_teacher(actor):
        return
    raise HTTPException(status_code=403, detail="Only teacher owner or admin can manage this resource")


async def _ensure_teacher_profile(db: AsyncSession, teacher_user_id: int) -> TeacherProfile:
    row = (await db.execute(select(TeacherProfile).where(TeacherProfile.user_id == teacher_user_id))).scalar_one_or_none()
    if row is None:
        row = TeacherProfile(user_id=teacher_user_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _ensure_student_balance(db: AsyncSession, student_user_id: int) -> StudentBalance:
    row = (await db.execute(select(StudentBalance).where(StudentBalance.user_id == student_user_id))).scalar_one_or_none()
    if row is None:
        row = StudentBalance(user_id=student_user_id, balance=500)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _user_by_wp_id(db: AsyncSession, wp_user_id: int) -> UserShadow:
    row = (await db.execute(select(UserShadow).where(UserShadow.wp_user_id == wp_user_id))).scalar_one_or_none()
    if row is None:
        row = await _sync_from_wp(db, wp_user_id)
    return row


async def _wp_id_map(db: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = (await db.execute(select(UserShadow).where(UserShadow.id.in_(user_ids)))).scalars().all()
    return {r.id: r.wp_user_id for r in rows}


def _to_subscription_out(sub: TeacherStudentSubscription, teacher: UserShadow, student: UserShadow) -> SubscriptionOut:
    return SubscriptionOut(
        id=sub.id,
        teacher_wp_id=teacher.wp_user_id,
        teacher_name=teacher.display_name,
        student_wp_id=student.wp_user_id,
        student_name=student.display_name,
        months=sub.months,
        sessions_per_month=sub.sessions_per_month,
        points_cost=sub.points_cost,
        status=sub.status,
        starts_at=sub.starts_at,
        ends_at=sub.ends_at,
    )


def _to_teacher_session_out(row: TeacherSession, teacher_wp_user_id: int, student_wp_user_id: int | None) -> TeacherSessionOut:
    return TeacherSessionOut(
        id=row.id,
        teacher_wp_id=teacher_wp_user_id,
        student_wp_user_id=student_wp_user_id,
        title=row.title,
        kind=row.kind,
        status=row.status,
        starts_at=row.starts_at,
        duration_minutes=row.duration_minutes,
        meeting_url=row.meeting_url,
        access_url=_session_access_url(row),
    )


async def _publish_session_event(
    *,
    row: TeacherSession,
    event_type: str,
    teacher_wp_user_id: int,
    student_wp_user_id: int | None,
    actor: UserShadow | None = None,
    actor_avatar_url: str | None = None,
    extra: dict | None = None,
) -> None:
    payload: dict = {
        "type": event_type,
        "session_id": row.id,
        "event_at": _utc_now().isoformat(),
        "session": jsonable_encoder(
            _to_teacher_session_out(
                row,
                teacher_wp_user_id=teacher_wp_user_id,
                student_wp_user_id=student_wp_user_id,
            )
        ),
    }
    if actor is not None:
        payload["actor"] = {
            "wp_user_id": actor.wp_user_id,
            "display_name": actor.display_name,
            "role_tag": _role_tag_from_roles(actor.roles),
            "avatar_url": (actor_avatar_url or "").strip() or None,
        }
    if isinstance(extra, dict) and extra:
        payload.update(extra)

    try:
        await ws_manager.publish(f"session:{row.id}", payload)
    except Exception:
        logger.exception("Failed to publish realtime event for session id=%s", row.id)


def _to_event(
    row: TeacherSession,
    teacher: UserShadow,
    student_wp_user_id: int | None,
) -> CalendarEventOut:
    return CalendarEventOut(
        id=f"teacher-session-{row.id}",
        session_id=row.id,
        type="teacher_session",
        kind=row.kind,
        title=row.title,
        teacher_wp_id=teacher.wp_user_id,
        student_wp_user_id=student_wp_user_id,
        teacher_name=teacher.display_name,
        starts_at=row.starts_at,
        duration_minutes=row.duration_minutes,
        status=row.status,
    )


async def _active_subscription(
    db: AsyncSession,
    *,
    teacher_user_id: int,
    student_user_id: int,
) -> TeacherStudentSubscription | None:
    return (
        await db.execute(
            select(TeacherStudentSubscription).where(
                and_(
                    TeacherStudentSubscription.teacher_user_id == teacher_user_id,
                    TeacherStudentSubscription.student_user_id == student_user_id,
                    TeacherStudentSubscription.status == "active",
                )
            )
        )
    ).scalar_one_or_none()


async def _ensure_session_access(db: AsyncSession, *, actor: UserShadow, row: TeacherSession) -> None:
    if _is_admin(actor) or actor.id == row.teacher_user_id:
        return

    if row.target_student_user_id is not None:
        if actor.id != row.target_student_user_id:
            raise HTTPException(status_code=403, detail="Session reserved for assigned student")
        active = await _active_subscription(db, teacher_user_id=row.teacher_user_id, student_user_id=actor.id)
        if active is None:
            raise HTTPException(status_code=403, detail="Subscription required")
        return

    active = await _active_subscription(db, teacher_user_id=row.teacher_user_id, student_user_id=actor.id)
    if active is None:
        raise HTTPException(status_code=403, detail="Only subscribed students can access this session")


def _refresh_live_status(row: TeacherSession) -> None:
    if row.kind != "live":
        return
    now = _utc_now()
    starts_at = row.starts_at
    ends_at = starts_at + timedelta(minutes=max(int(row.duration_minutes or 60), 1))
    if now >= ends_at:
        row.status = "ended"
        return
    if now + timedelta(minutes=10) >= starts_at:
        row.status = "live"
    else:
        row.status = "scheduled"


async def _refresh_live_statuses(db: AsyncSession, rows: list[TeacherSession]) -> None:
    dirty = False
    for row in rows:
        before = row.status
        _refresh_live_status(row)
        if row.status != before:
            dirty = True
    if dirty:
        await db.commit()


def _session_ends_at(row: TeacherSession) -> datetime:
    return row.starts_at + timedelta(minutes=max(int(row.duration_minutes or 60), 1))


def _should_prune_ended_live(row: TeacherSession, *, now: datetime) -> bool:
    if str(row.kind or "").lower() != "live":
        return False
    if str(row.status or "").lower() != "ended":
        return False
    grace_minutes = max(int(settings.live_session_cleanup_minutes or 0), 0)
    cutoff = now - timedelta(minutes=grace_minutes)
    return _session_ends_at(row) <= cutoff


async def _prune_ended_lives(
    db: AsyncSession,
    *,
    teacher_user_id: int | None = None,
    teacher_user_ids: list[int] | None = None,
) -> int:
    stmt = select(TeacherSession).where(
        and_(
            TeacherSession.kind == "live",
            TeacherSession.status == "ended",
        )
    )
    selected_teacher_ids: set[int] = set()
    if teacher_user_id is not None:
        selected_teacher_ids.add(int(teacher_user_id))
    if teacher_user_ids:
        selected_teacher_ids.update(int(x) for x in teacher_user_ids if x is not None)
    if selected_teacher_ids:
        stmt = stmt.where(TeacherSession.teacher_user_id.in_(sorted(selected_teacher_ids)))

    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return 0

    now = _utc_now()
    ids_to_delete = [row.id for row in rows if _should_prune_ended_live(row, now=now)]
    if not ids_to_delete:
        return 0

    await db.execute(delete(TeacherSession).where(TeacherSession.id.in_(ids_to_delete)))
    await db.commit()
    return len(ids_to_delete)


def _filter_dashboard_sessions(rows: list[TeacherSession], *, include_history: bool) -> list[TeacherSession]:
    if include_history:
        return rows
    return [
        row
        for row in rows
        if not (str(row.kind or "").lower() == "live" and str(row.status or "").lower() == "ended")
    ]


async def _create_subscription(
    db: AsyncSession,
    *,
    teacher: UserShadow,
    student: UserShadow,
    months: int,
    sessions_per_month: int,
    charge_balance: bool,
) -> TeacherStudentSubscription:
    existing = await _active_subscription(db, teacher_user_id=teacher.id, student_user_id=student.id)
    if existing is not None:
        return existing

    cost = _calc_points_cost(months, sessions_per_month)
    ledger_note: str | None = None
    if charge_balance:
        balance = await _ensure_student_balance(db, student.id)
        if balance.balance < cost:
            raise HTTPException(status_code=400, detail="Insufficient points balance")
        balance.balance -= cost
        ledger_note = f"Subscription points debit ({cost} pts)"

    now = _utc_now()
    sub = TeacherStudentSubscription(
        teacher_user_id=teacher.id,
        student_user_id=student.id,
        months=months,
        sessions_per_month=sessions_per_month,
        points_cost=cost,
        status="active",
        starts_at=now,
        ends_at=now + timedelta(days=30 * months),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)

    if charge_balance:
        await _record_wallet_ledger(
            db,
            student_user_id=student.id,
            direction="debit",
            amount_cents=cost * 100,
            points_delta=-cost,
            source="points_subscription",
            reference_type="subscription",
            reference_id=str(sub.id),
            note=ledger_note,
        )
        await db.commit()
    return sub


async def _record_points_subscription_payment(
    db: AsyncSession,
    *,
    teacher: UserShadow,
    student: UserShadow,
    sub: TeacherStudentSubscription,
) -> PaymentTransaction:
    existing = (
        await db.execute(
            select(PaymentTransaction).where(
                and_(
                    PaymentTransaction.subscription_id == sub.id,
                    PaymentTransaction.provider == "wallet_points",
                    PaymentTransaction.status == "paid",
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    amount_cents = max(int(sub.points_cost or 0), 0) * 100
    teacher_earnings_cents, platform_fee_cents = _calc_teacher_earnings_cents(amount_cents)
    row = PaymentTransaction(
        student_user_id=student.id,
        teacher_user_id=teacher.id,
        subscription_id=sub.id,
        months=sub.months,
        sessions_per_month=sub.sessions_per_month,
        amount_cents=amount_cents,
        currency="EUR",
        provider="wallet_points",
        status="paid",
        checkout_token=f"wallet_points_{uuid.uuid4().hex}",
        checkout_url=f"wallet://points/subscription/{sub.id}",
        paid_at=_utc_now(),
        teacher_earnings_cents=teacher_earnings_cents,
        platform_fee_cents=platform_fee_cents,
    )
    db.add(row)
    await db.flush()
    await _credit_teacher_wallet_from_payment(
        db,
        payment=row,
        teacher_wp_user_id=teacher.wp_user_id,
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/teachers", response_model=list[TeacherOut])
async def list_teachers(
    db: AsyncSession = Depends(get_db),
    _actor: UserShadow | None = Depends(get_request_user_optional),
) -> list[TeacherOut]:
    try:
        roles_to_sync = list(
            dict.fromkeys(
                [
                    (settings.wp_role_teacher or "prof").strip().lower(),
                    "teacher",
                ]
            )
        )
        wp_map: dict[int, dict] = {}
        for role in roles_to_sync:
            for teacher in await fetch_wp_users_by_role(role):
                wp_map[int(teacher["id"])] = teacher
    except Exception as exc:
        logger.exception("Failed syncing teachers from WordPress")
        raise HTTPException(status_code=502, detail="WordPress API unavailable") from exc

    if not wp_map:
        return []

    teachers: list[UserShadow] = []
    for teacher in wp_map.values():
        row = await _upsert_user_shadow(
            db,
            wp_user_id=int(teacher["id"]),
            email=str(teacher.get("email") or "").strip().lower(),
            display_name=str(teacher.get("display_name") or "").strip(),
            roles=[str(x).strip().lower() for x in (teacher.get("roles") or [])],
            avatar_url=str(teacher.get("avatar_url") or "").strip() or None,
        )
        teachers.append(row)

    profiles = (
        await db.execute(select(TeacherProfile).where(TeacherProfile.user_id.in_([x.id for x in teachers])))
    ).scalars().all()
    profile_map = {p.user_id: p for p in profiles}
    user_profiles = (
        await db.execute(select(Profile).where(Profile.user_id.in_([x.id for x in teachers])))
    ).scalars().all()
    user_profile_map = {p.user_id: p for p in user_profiles}

    out: list[TeacherOut] = []
    for teacher in sorted(teachers, key=lambda x: (x.display_name or "").lower()):
        profile = profile_map.get(teacher.id)
        user_profile = user_profile_map.get(teacher.id)
        wp_payload = wp_map.get(teacher.wp_user_id) or {}
        avatar_url = (
            str(wp_payload.get("avatar_url") or "").strip()
            or str((user_profile.avatar_url if user_profile else "") or "").strip()
            or None
        )
        out.append(
            TeacherOut(
                wp_user_id=teacher.wp_user_id,
                display_name=teacher.display_name,
                avatar_url=avatar_url,
                city=(profile.city if profile and profile.city else ""),
                subjects=(profile.subjects if profile and profile.subjects else ""),
                hourly_rate=(profile.hourly_rate if profile else None),
            )
        )
    return out


@router.post("/teachers/{teacher_wp_user_id}/subscribe", response_model=SubscriptionOut)
async def subscribe_teacher(
    teacher_wp_user_id: int,
    payload: TeacherSubscribeIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> SubscriptionOut:
    if actor.wp_user_id == teacher_wp_user_id:
        raise HTTPException(status_code=400, detail="Cannot subscribe to yourself")
    if not (_is_student(actor) or _is_admin(actor)):
        raise HTTPException(status_code=403, detail="Only students can subscribe")

    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    existing = await _active_subscription(db, teacher_user_id=teacher.id, student_user_id=actor.id)
    if existing is not None:
        return _to_subscription_out(existing, teacher, actor)

    sub = await _create_subscription(
        db,
        teacher=teacher,
        student=actor,
        months=payload.months,
        sessions_per_month=payload.sessions_per_month,
        charge_balance=True,
    )
    await _record_points_subscription_payment(
        db,
        teacher=teacher,
        student=actor,
        sub=sub,
    )
    return _to_subscription_out(sub, teacher, actor)


@router.get("/students/{student_wp_user_id}/balance", response_model=StudentBalanceOut)
async def get_student_balance(
    student_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> StudentBalanceOut:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)
    row = await _ensure_student_balance(db, student.id)
    return StudentBalanceOut(student_wp_id=student.wp_user_id, balance=row.balance)


@router.get("/students/{student_wp_user_id}/money", response_model=StudentMoneyOut)
async def get_student_money(
    student_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> StudentMoneyOut:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)
    balance = await _ensure_student_balance(db, student.id)
    ledger_rows = (
        await db.execute(
            select(WalletLedger)
            .where(WalletLedger.student_user_id == student.id)
            .order_by(desc(WalletLedger.created_at))
        )
    ).scalars().all()
    payment_rows = (
        await db.execute(
            select(PaymentTransaction)
            .where(PaymentTransaction.student_user_id == student.id)
            .order_by(desc(PaymentTransaction.created_at))
        )
    ).scalars().all()

    deposited = sum(max(int(x.amount_cents or 0), 0) for x in ledger_rows if x.direction == "credit")
    spent = sum(max(int(x.amount_cents or 0), 0) for x in ledger_rows if x.direction == "debit")
    refunded = sum(
        max(int(tx.amount_cents or 0), 0) for tx in payment_rows if str(tx.status or "").lower() in {"refunded", "refund"}
    )
    paid_count = sum(1 for tx in payment_rows if str(tx.status or "").lower() == "paid")

    return StudentMoneyOut(
        student_wp_id=student.wp_user_id,
        currency="EUR",
        deposited_cents=deposited,
        spent_cents=spent,
        refunded_cents=refunded,
        paid_transactions=paid_count,
        points_balance=balance.balance,
    )


@router.get("/teachers/{teacher_wp_user_id}/earnings", response_model=TeacherEarningsOut)
async def get_teacher_earnings(
    teacher_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherEarningsOut:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    rows = (
        await db.execute(
            select(PaymentTransaction)
            .where(
                and_(
                    PaymentTransaction.teacher_user_id == teacher.id,
                    PaymentTransaction.status == "paid",
                )
            )
            .order_by(desc(PaymentTransaction.created_at))
        )
    ).scalars().all()

    gross = 0
    earnings = 0
    platform_fee = 0
    for tx in rows:
        amount = max(int(tx.amount_cents or 0), 0)
        gross += amount
        if tx.teacher_earnings_cents or tx.platform_fee_cents:
            earnings += max(int(tx.teacher_earnings_cents or 0), 0)
            platform_fee += max(int(tx.platform_fee_cents or 0), 0)
            continue
        tx_earnings, tx_fee = _calc_teacher_earnings_cents(amount)
        earnings += tx_earnings
        platform_fee += tx_fee

    return TeacherEarningsOut(
        teacher_wp_id=teacher.wp_user_id,
        currency="EUR",
        gross_cents=gross,
        earnings_cents=earnings,
        platform_fee_cents=platform_fee,
        paid_transactions=len(rows),
    )


@router.get("/admin/revenue/summary", response_model=PlatformRevenueOut)
async def get_admin_revenue_summary(
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> PlatformRevenueOut:
    if not _is_admin(actor):
        raise HTTPException(status_code=403, detail="Admin only")

    rows = (
        await db.execute(
            select(PaymentTransaction)
            .where(PaymentTransaction.status == "paid")
            .order_by(desc(PaymentTransaction.created_at))
        )
    ).scalars().all()

    gross = 0
    teacher_earnings = 0
    platform_fee = 0
    for tx in rows:
        amount = max(int(tx.amount_cents or 0), 0)
        gross += amount
        tx_earnings = max(int(tx.teacher_earnings_cents or 0), 0)
        tx_fee = max(int(tx.platform_fee_cents or 0), 0)
        if tx_earnings == 0 and tx_fee == 0 and amount > 0:
            tx_earnings, tx_fee = _calc_teacher_earnings_cents(amount)
        teacher_earnings += tx_earnings
        platform_fee += tx_fee

    return PlatformRevenueOut(
        currency="EUR",
        gross_cents=gross,
        teacher_earnings_cents=teacher_earnings,
        platform_fee_cents=platform_fee,
        paid_transactions=len(rows),
    )


@router.get("/teachers/{teacher_wp_user_id}/wallet", response_model=TeacherWalletOut)
async def get_teacher_wallet(
    teacher_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherWalletOut:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    await _sync_teacher_wallet_from_paid_transactions(db, teacher=teacher)

    total_earned = (
        await db.execute(
            select(func.coalesce(func.sum(PaymentTransaction.teacher_earnings_cents), 0)).where(
                and_(
                    PaymentTransaction.teacher_user_id == teacher.id,
                    PaymentTransaction.status == "paid",
                )
            )
        )
    ).scalar_one()
    total_withdrawn = (
        await db.execute(
            select(func.coalesce(func.sum(TeacherWithdrawalRequest.amount_cents), 0)).where(
                and_(
                    TeacherWithdrawalRequest.teacher_user_id == teacher.id,
                    TeacherWithdrawalRequest.status == "paid",
                )
            )
        )
    ).scalar_one()
    pending_withdrawals = await _teacher_pending_withdrawals_cents(db, teacher.id)
    available = await _teacher_wallet_net_cents(db, teacher.id)

    return TeacherWalletOut(
        teacher_wp_id=teacher.wp_user_id,
        currency="EUR",
        total_earned_cents=int(total_earned or 0),
        total_withdrawn_cents=int(total_withdrawn or 0),
        pending_withdrawals_cents=max(int(pending_withdrawals), 0),
        available_cents=max(int(available), 0),
    )


@router.get("/teachers/{teacher_wp_user_id}/wallet/ledger", response_model=list[TeacherWalletLedgerOut])
async def list_teacher_wallet_ledger(
    teacher_wp_user_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[TeacherWalletLedgerOut]:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    await _sync_teacher_wallet_from_paid_transactions(db, teacher=teacher)

    rows = (
        await db.execute(
            select(TeacherWalletLedger)
            .where(TeacherWalletLedger.teacher_user_id == teacher.id)
            .order_by(desc(TeacherWalletLedger.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [
        TeacherWalletLedgerOut(
            id=row.id,
            direction=row.direction,
            amount_cents=row.amount_cents,
            source=row.source,
            reference_type=row.reference_type,
            reference_id=row.reference_id,
            note=row.note,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/teachers/{teacher_wp_user_id}/withdrawals", response_model=TeacherWithdrawOut)
async def create_teacher_withdrawal(
    teacher_wp_user_id: int,
    payload: TeacherWithdrawCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherWithdrawOut:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    await _sync_teacher_wallet_from_paid_transactions(db, teacher=teacher)

    method = (payload.method or "paypal").strip().lower()
    if method not in _WITHDRAW_ALLOWED_METHODS:
        raise HTTPException(status_code=400, detail="method must be paypal, manual or bank")

    available = await _teacher_wallet_net_cents(db, teacher.id)
    amount = int(payload.amount_cents or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid withdraw amount")
    if amount > available:
        raise HTTPException(status_code=400, detail="Insufficient teacher wallet balance")

    paypal_email = (payload.paypal_email or "").strip().lower() or None
    if method == "paypal" and not paypal_email:
        paypal_email = (teacher.email or "").strip().lower() or None

    req = TeacherWithdrawalRequest(
        teacher_user_id=teacher.id,
        amount_cents=amount,
        currency="EUR",
        method=method,
        paypal_email=paypal_email,
        status="pending",
        note=(payload.note or "").strip() or None,
    )
    db.add(req)
    await db.flush()
    await _record_teacher_wallet_ledger(
        db,
        teacher_user_id=teacher.id,
        direction="debit",
        amount_cents=amount,
        source="withdraw_request_hold",
        reference_type="withdrawal_request",
        reference_id=str(req.id),
        note=f"Withdrawal request #{req.id} hold",
    )
    await db.commit()
    await db.refresh(req)
    return _to_teacher_withdraw_out(req, teacher)


@router.get("/teachers/{teacher_wp_user_id}/withdrawals", response_model=list[TeacherWithdrawOut])
async def list_teacher_withdrawals(
    teacher_wp_user_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[TeacherWithdrawOut]:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    rows = (
        await db.execute(
            select(TeacherWithdrawalRequest)
            .where(TeacherWithdrawalRequest.teacher_user_id == teacher.id)
            .order_by(desc(TeacherWithdrawalRequest.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [_to_teacher_withdraw_out(row, teacher) for row in rows]


@router.patch("/teachers/{teacher_wp_user_id}/withdrawals/{withdrawal_id}", response_model=TeacherWithdrawOut)
async def update_teacher_withdrawal(
    teacher_wp_user_id: int,
    withdrawal_id: int,
    payload: TeacherWithdrawUpdateIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherWithdrawOut:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    row = (
        await db.execute(
            select(TeacherWithdrawalRequest).where(
                and_(
                    TeacherWithdrawalRequest.id == withdrawal_id,
                    TeacherWithdrawalRequest.teacher_user_id == teacher.id,
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Withdrawal request not found")

    new_status = (payload.status or "").strip().lower()
    if new_status not in _WITHDRAW_ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid withdrawal status")

    is_admin = _is_admin(actor)
    if not is_admin:
        if actor.id != teacher.id:
            raise HTTPException(status_code=403, detail="Forbidden")
        if new_status != "cancelled":
            raise HTTPException(status_code=403, detail="Teacher can only cancel withdrawal")
        if row.status != "pending":
            raise HTTPException(status_code=400, detail="Only pending withdrawal can be cancelled")

    if row.status in {"paid", "rejected", "cancelled"} and new_status != row.status:
        raise HTTPException(status_code=400, detail="Finalized withdrawal cannot change status")

    if is_admin:
        row.admin_note = (payload.admin_note or "").strip() or row.admin_note
        row.external_ref = (payload.external_ref or "").strip() or row.external_ref

    if new_status == "paid" and row.status in {"pending", "processing"}:
        if row.method == "paypal" and not row.external_ref:
            if not row.paypal_email:
                raise HTTPException(status_code=400, detail="PayPal withdrawal needs paypal_email")
            if not (settings.paypal_client_id and settings.paypal_client_secret):
                raise HTTPException(
                    status_code=400,
                    detail="PayPal payout is not configured on server (missing credentials)",
                )
            try:
                payout = await create_paypal_payout(
                    client_id=settings.paypal_client_id,
                    client_secret=settings.paypal_client_secret,
                    env=settings.paypal_env,
                    receiver_email=row.paypal_email,
                    amount_cents=row.amount_cents,
                    currency=row.currency,
                    note=row.note or f"HomeBook teacher withdrawal #{row.id}",
                    sender_item_id=f"hb-wd-{row.id}-{uuid.uuid4().hex[:10]}",
                )
            except PaymentProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            row.external_ref = (
                str(payout.get("payout_item_id") or "").strip()
                or str(payout.get("payout_batch_id") or "").strip()
                or row.external_ref
            )
            if not row.admin_note:
                payout_status = str(payout.get("payout_status") or "").strip()
                if payout_status:
                    row.admin_note = f"PayPal payout status: {payout_status}"

    if new_status in {"rejected", "cancelled"} and row.status in {"pending", "processing"}:
        await _record_teacher_wallet_ledger(
            db,
            teacher_user_id=teacher.id,
            direction="credit",
            amount_cents=row.amount_cents,
            source="withdraw_request_reversal",
            reference_type="withdrawal_request_reversal",
            reference_id=str(row.id),
            note=f"Withdrawal #{row.id} reversed",
        )

    row.status = new_status
    if new_status in {"paid", "rejected", "cancelled"}:
        row.processed_at = _utc_now()
    elif new_status in {"pending", "processing"}:
        row.processed_at = None

    await db.commit()
    await db.refresh(row)
    return _to_teacher_withdraw_out(row, teacher)


@router.get("/users/{wp_user_id}/subscriptions", response_model=list[SubscriptionOut])
async def list_user_subscriptions(
    wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[SubscriptionOut]:
    _ensure_self_or_admin(actor, wp_user_id)
    student = await _user_by_wp_id(db, wp_user_id)

    rows = (
        await db.execute(
            select(TeacherStudentSubscription, UserShadow)
            .join(UserShadow, UserShadow.id == TeacherStudentSubscription.teacher_user_id)
            .where(TeacherStudentSubscription.student_user_id == student.id)
            .order_by(desc(TeacherStudentSubscription.starts_at))
        )
    ).all()

    return [_to_subscription_out(sub, teacher, student) for sub, teacher in rows]


@router.get("/teachers/{teacher_wp_user_id}/students", response_model=list[TeacherStudentOut])
async def list_teacher_students(
    teacher_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[TeacherStudentOut]:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)

    rows = (
        await db.execute(
            select(TeacherStudentSubscription, UserShadow)
            .join(UserShadow, UserShadow.id == TeacherStudentSubscription.student_user_id)
            .where(TeacherStudentSubscription.teacher_user_id == teacher.id)
            .order_by(desc(TeacherStudentSubscription.starts_at))
        )
    ).all()

    return [
        TeacherStudentOut(
            wp_user_id=student.wp_user_id,
            display_name=student.display_name,
            email=student.email,
            status=sub.status,
            months=sub.months,
            sessions_per_month=sub.sessions_per_month,
            starts_at=sub.starts_at,
            ends_at=sub.ends_at,
        )
        for sub, student in rows
    ]


@router.post("/teachers/{teacher_wp_user_id}/sessions", response_model=TeacherSessionOut)
async def create_teacher_session(
    teacher_wp_user_id: int,
    payload: TeacherSessionCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherSessionOut:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    await _ensure_teacher_profile(db, teacher.id)

    kind = payload.kind.strip().lower()
    if kind not in {"live", "course"}:
        raise HTTPException(status_code=400, detail="kind must be 'live' or 'course'")

    target_student_user_id: int | None = None
    target_student_wp_user_id: int | None = None
    if payload.student_wp_user_id is not None:
        student = await _user_by_wp_id(db, payload.student_wp_user_id)
        active = await _active_subscription(db, teacher_user_id=teacher.id, student_user_id=student.id)
        if active is None:
            raise HTTPException(status_code=400, detail="Student is not subscribed to this teacher")
        target_student_user_id = student.id
        target_student_wp_user_id = student.wp_user_id

    status = "scheduled"
    if kind == "live":
        now = _utc_now()
        ends_at = payload.starts_at + timedelta(minutes=max(int(payload.duration_minutes or 60), 1))
        if now >= ends_at:
            status = "ended"
        elif now + timedelta(minutes=10) >= payload.starts_at:
            status = "live"

    row = TeacherSession(
        teacher_user_id=teacher.id,
        target_student_user_id=target_student_user_id,
        title=payload.title.strip(),
        kind=kind,
        status=status,
        starts_at=payload.starts_at,
        duration_minutes=payload.duration_minutes,
        meeting_url=(payload.meeting_url.strip() if payload.meeting_url else _default_meeting_url(kind)),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _publish_session_event(
        row=row,
        event_type="session.created",
        teacher_wp_user_id=teacher.wp_user_id,
        student_wp_user_id=target_student_wp_user_id,
        actor=actor,
    )
    return _to_teacher_session_out(row, teacher.wp_user_id, target_student_wp_user_id)


@router.get("/teachers/{teacher_wp_user_id}/sessions", response_model=list[TeacherSessionOut])
async def list_teacher_sessions(
    teacher_wp_user_id: int,
    include_history: bool = Query(default=False),
    auto_cleanup: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[TeacherSessionOut]:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)
    stmt = (
        select(TeacherSession)
        .where(TeacherSession.teacher_user_id == teacher.id)
        .order_by(desc(TeacherSession.starts_at))
    )
    rows = (await db.execute(stmt)).scalars().all()
    await _refresh_live_statuses(db, rows)
    if auto_cleanup and not include_history:
        deleted = await _prune_ended_lives(db, teacher_user_id=teacher.id)
        if deleted > 0:
            rows = (await db.execute(stmt)).scalars().all()
    rows = _filter_dashboard_sessions(rows, include_history=include_history)
    wp_map = await _wp_id_map(db, [r.target_student_user_id for r in rows if r.target_student_user_id is not None])
    return [_to_teacher_session_out(row, teacher.wp_user_id, wp_map.get(row.target_student_user_id)) for row in rows]


@router.get("/students/{student_wp_user_id}/sessions", response_model=list[TeacherSessionOut])
async def list_student_sessions(
    student_wp_user_id: int,
    include_history: bool = Query(default=False),
    auto_cleanup: bool = Query(default=True),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[TeacherSessionOut]:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)

    active_subs = (
        await db.execute(
            select(TeacherStudentSubscription).where(
                and_(
                    TeacherStudentSubscription.student_user_id == student.id,
                    TeacherStudentSubscription.status == "active",
                )
            )
        )
    ).scalars().all()
    teacher_ids = [x.teacher_user_id for x in active_subs]
    if not teacher_ids:
        return []

    stmt = (
        select(TeacherSession, UserShadow)
        .join(UserShadow, UserShadow.id == TeacherSession.teacher_user_id)
        .where(
            and_(
                TeacherSession.teacher_user_id.in_(teacher_ids),
                or_(
                    TeacherSession.target_student_user_id.is_(None),
                    TeacherSession.target_student_user_id == student.id,
                ),
            )
        )
        .order_by(TeacherSession.starts_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    await _refresh_live_statuses(db, [row for row, _teacher in rows])
    if auto_cleanup and not include_history:
        deleted = await _prune_ended_lives(db, teacher_user_ids=teacher_ids)
        if deleted > 0:
            rows = (await db.execute(stmt)).all()
    rows = [
        (row, teacher)
        for row, teacher in rows
        if include_history or not (str(row.kind or "").lower() == "live" and str(row.status or "").lower() == "ended")
    ]
    return [
        _to_teacher_session_out(row, teacher.wp_user_id, student.wp_user_id if row.target_student_user_id else None)
        for row, teacher in rows
    ]


@router.get("/sessions/{session_id}", response_model=TeacherSessionOut)
async def get_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherSessionOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _ensure_session_access(db, actor=actor, row=row)
    await _refresh_live_statuses(db, [row])
    teacher = (await db.execute(select(UserShadow).where(UserShadow.id == row.teacher_user_id))).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    student_wp_id = None
    if row.target_student_user_id is not None:
        student = (
            await db.execute(select(UserShadow).where(UserShadow.id == row.target_student_user_id))
        ).scalar_one_or_none()
        student_wp_id = student.wp_user_id if student else None
    return _to_teacher_session_out(row, teacher.wp_user_id, student_wp_id)


async def _delete_session_with_permissions(
    *,
    session_id: int,
    db: AsyncSession,
    actor: UserShadow,
) -> None:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not (_is_admin(actor) or actor.id == row.teacher_user_id):
        raise HTTPException(status_code=403, detail="Only teacher owner or admin can delete this session")

    teacher = (await db.execute(select(UserShadow).where(UserShadow.id == row.teacher_user_id))).scalar_one_or_none()
    if teacher is None:
        raise HTTPException(status_code=404, detail="Teacher not found")

    student_wp_id = None
    if row.target_student_user_id is not None:
        student = (await db.execute(select(UserShadow).where(UserShadow.id == row.target_student_user_id))).scalar_one_or_none()
        student_wp_id = student.wp_user_id if student else None

    session_snapshot = jsonable_encoder(
        _to_teacher_session_out(
            row,
            teacher_wp_user_id=teacher.wp_user_id,
            student_wp_user_id=student_wp_id,
        )
    )

    await db.delete(row)
    await db.commit()

    payload: dict = {
        "type": "session.deleted",
        "session_id": session_id,
        "event_at": _utc_now().isoformat(),
        "session": session_snapshot,
        "actor": {
            "wp_user_id": actor.wp_user_id,
            "display_name": actor.display_name,
            "role_tag": _role_tag_from_roles(actor.roles),
            "avatar_url": None,
        },
    }
    try:
        await ws_manager.publish(f"session:{session_id}", payload)
    except Exception:
        logger.exception("Failed to publish realtime delete event for session id=%s", session_id)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> Response:
    await _delete_session_with_permissions(session_id=session_id, db=db, actor=actor)
    return Response(status_code=204)


@router.post("/sessions/{session_id}/delete", status_code=204)
async def delete_session_post_fallback(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> Response:
    await _delete_session_with_permissions(session_id=session_id, db=db, actor=actor)
    return Response(status_code=204)


@router.patch("/sessions/{session_id}/schedule", response_model=TeacherSessionOut)
async def update_session_schedule(
    session_id: int,
    payload: SessionScheduleUpdateIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> TeacherSessionOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")

    can_edit = _is_admin(actor) or actor.id == row.teacher_user_id
    if not can_edit and _is_student(actor):
        if row.target_student_user_id is not None and actor.id == row.target_student_user_id:
            active = await _active_subscription(db, teacher_user_id=row.teacher_user_id, student_user_id=actor.id)
            can_edit = active is not None
        elif row.target_student_user_id is None and row.kind == "course":
            active = await _active_subscription(db, teacher_user_id=row.teacher_user_id, student_user_id=actor.id)
            can_edit = active is not None
    if not can_edit:
        raise HTTPException(status_code=403, detail="You cannot edit this schedule")

    if payload.starts_at is None and payload.duration_minutes is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if payload.starts_at is not None:
        row.starts_at = payload.starts_at
        if row.kind != "live":
            row.status = "scheduled"
    if payload.duration_minutes is not None:
        row.duration_minutes = payload.duration_minutes
    if row.kind == "live":
        _refresh_live_status(row)

    await db.commit()
    await db.refresh(row)

    teacher = (await db.execute(select(UserShadow).where(UserShadow.id == row.teacher_user_id))).scalar_one()
    student_wp_id = None
    if row.target_student_user_id is not None:
        student = (await db.execute(select(UserShadow).where(UserShadow.id == row.target_student_user_id))).scalar_one_or_none()
        student_wp_id = student.wp_user_id if student else None
    await _publish_session_event(
        row=row,
        event_type="session.updated",
        teacher_wp_user_id=teacher.wp_user_id,
        student_wp_user_id=student_wp_id,
        actor=actor,
    )
    return _to_teacher_session_out(row, teacher.wp_user_id, student_wp_id)


@router.post("/sessions/{session_id}/join", response_model=LiveJoinOut)
async def join_live_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> LiveJoinOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _ensure_session_meeting_url(db, row)
    await _ensure_session_access(db, actor=actor, row=row)
    if row.kind == "live":
        _refresh_live_status(row)
        await db.commit()
        await db.refresh(row)
    teacher = (await db.execute(select(UserShadow).where(UserShadow.id == row.teacher_user_id))).scalar_one_or_none()
    student_wp_id = None
    if row.target_student_user_id is not None:
        student = (
            await db.execute(select(UserShadow).where(UserShadow.id == row.target_student_user_id))
        ).scalar_one_or_none()
        student_wp_id = student.wp_user_id if student else None
    if teacher is not None:
        await _publish_session_event(
            row=row,
            event_type="session.joined",
            teacher_wp_user_id=teacher.wp_user_id,
            student_wp_user_id=student_wp_id,
            actor=actor,
        )
    join_url = _session_access_url(row)
    return LiveJoinOut(session_id=row.id, join_url=join_url, status=row.status, kind=row.kind)


@router.get("/sessions/{session_id}/access", response_model=SessionAccessOut)
async def get_session_access(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> SessionAccessOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _ensure_session_meeting_url(db, row)
    await _ensure_session_access(db, actor=actor, row=row)
    if row.kind == "live":
        _refresh_live_status(row)
        await db.commit()
        await db.refresh(row)
    return SessionAccessOut(
        session_id=row.id,
        access_url=_session_access_url(row),
        status=row.status,
        kind=row.kind,
    )


@router.get("/teachers/{teacher_wp_user_id}/calendar", response_model=list[CalendarEventOut])
async def teacher_calendar(
    teacher_wp_user_id: int,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[CalendarEventOut]:
    _ensure_teacher_owner_or_admin(actor, teacher_wp_user_id)
    teacher = await _user_by_wp_id(db, teacher_wp_user_id)

    stmt = select(TeacherSession).where(TeacherSession.teacher_user_id == teacher.id)
    if date_from:
        stmt = stmt.where(TeacherSession.starts_at >= date_from)
    if date_to:
        stmt = stmt.where(TeacherSession.starts_at <= date_to)
    stmt = stmt.order_by(TeacherSession.starts_at.asc())
    rows = (await db.execute(stmt)).scalars().all()
    await _refresh_live_statuses(db, rows)
    student_map = await _wp_id_map(db, [x.target_student_user_id for x in rows if x.target_student_user_id is not None])
    return [_to_event(x, teacher, student_map.get(x.target_student_user_id)) for x in rows]


@router.get("/students/{student_wp_user_id}/calendar", response_model=list[CalendarEventOut])
async def student_calendar(
    student_wp_user_id: int,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[CalendarEventOut]:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)

    active_subs = (
        await db.execute(
            select(TeacherStudentSubscription).where(
                and_(
                    TeacherStudentSubscription.student_user_id == student.id,
                    TeacherStudentSubscription.status == "active",
                )
            )
        )
    ).scalars().all()
    teacher_ids = [x.teacher_user_id for x in active_subs]
    if not teacher_ids:
        return []

    teachers = (await db.execute(select(UserShadow).where(UserShadow.id.in_(teacher_ids)))).scalars().all()
    teacher_map = {t.id: t for t in teachers}

    stmt = select(TeacherSession).where(
        and_(
            TeacherSession.teacher_user_id.in_(teacher_ids),
            or_(
                TeacherSession.target_student_user_id.is_(None),
                TeacherSession.target_student_user_id == student.id,
            ),
        )
    )
    if date_from:
        stmt = stmt.where(TeacherSession.starts_at >= date_from)
    if date_to:
        stmt = stmt.where(TeacherSession.starts_at <= date_to)
    stmt = stmt.order_by(TeacherSession.starts_at.asc())
    sessions = (await db.execute(stmt)).scalars().all()
    await _refresh_live_statuses(db, sessions)

    out: list[CalendarEventOut] = []
    for row in sessions:
        teacher = teacher_map.get(row.teacher_user_id)
        if teacher is None:
            continue
        student_wp = student.wp_user_id if row.target_student_user_id else None
        out.append(_to_event(row, teacher, student_wp))
    return out


@router.post("/payments/checkouts", response_model=PaymentCheckoutOut)
async def create_subscription_checkout(
    payload: PaymentCheckoutIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> PaymentCheckoutOut:
    if not (_is_student(actor) or _is_admin(actor)):
        raise HTTPException(status_code=403, detail="Only students can start checkout")
    if actor.wp_user_id == payload.teacher_wp_id:
        raise HTTPException(status_code=400, detail="Cannot pay yourself")

    teacher = await _user_by_wp_id(db, payload.teacher_wp_id)
    amount_cents = _calc_points_cost(payload.months, payload.sessions_per_month) * 100
    selected_provider = (payload.provider or "auto").strip().lower()
    provider = "mock"
    token = uuid.uuid4().hex
    success_url = payload.success_url or _default_payment_success_url()
    cancel_url = payload.cancel_url or _default_payment_cancel_url()
    checkout_url = f"{success_url}?checkout_token={token}"
    provider_order_id: str | None = None

    has_stripe = bool(settings.stripe_secret_key)
    has_paypal = bool(settings.paypal_client_id and settings.paypal_client_secret)

    if selected_provider not in {"auto", "mock", "stripe", "paypal"}:
        raise HTTPException(status_code=400, detail="provider must be auto, mock, stripe or paypal")
    if selected_provider == "stripe" and not has_stripe:
        raise HTTPException(status_code=400, detail="Stripe is not configured on server")
    if selected_provider == "paypal" and not has_paypal:
        raise HTTPException(status_code=400, detail="PayPal is not configured on server")

    if selected_provider == "auto":
        if has_paypal:
            provider = "paypal"
        elif has_stripe:
            provider = "stripe"
    elif selected_provider in {"stripe", "paypal"}:
        provider = selected_provider

    if provider == "stripe":
        try:
            stripe_session = await create_stripe_checkout_session(
                secret_key=settings.stripe_secret_key,
                amount_cents=amount_cents,
                currency="EUR",
                title=f"HomeBook • Abonnement {teacher.display_name}",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "teacher_wp_id": str(teacher.wp_user_id),
                    "student_wp_id": str(actor.wp_user_id),
                    "months": str(payload.months),
                    "sessions_per_month": str(payload.sessions_per_month),
                },
            )
            token = stripe_session["session_id"]
            checkout_url = stripe_session["checkout_url"] or checkout_url
        except PaymentProviderError as exc:
            logger.exception("Stripe checkout creation failed")
            if selected_provider == "stripe":
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            provider = "mock"

    if provider == "paypal":
        try:
            paypal_order = await create_paypal_order(
                client_id=settings.paypal_client_id,
                client_secret=settings.paypal_client_secret,
                env=settings.paypal_env,
                amount_cents=amount_cents,
                currency="EUR",
                title=f"HomeBook • Abonnement {teacher.display_name}",
                return_url=success_url,
                cancel_url=cancel_url,
                custom_id=token,
            )
            token = paypal_order["order_id"]
            provider_order_id = paypal_order["order_id"]
            checkout_url = paypal_order["checkout_url"] or checkout_url
        except PaymentProviderError as exc:
            logger.exception("PayPal order creation failed")
            if selected_provider == "paypal":
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            provider = "mock"

    row = PaymentTransaction(
        student_user_id=actor.id,
        teacher_user_id=teacher.id,
        amount_cents=amount_cents,
        months=payload.months,
        sessions_per_month=payload.sessions_per_month,
        currency="EUR",
        provider=provider,
        status="pending",
        checkout_token=token,
        checkout_url=checkout_url,
        provider_order_id=provider_order_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return PaymentCheckoutOut(
        checkout_token=row.checkout_token,
        checkout_url=row.checkout_url,
        amount_cents=row.amount_cents,
        currency=row.currency,
        provider=row.provider,
        status=row.status,
    )


@router.post("/payments/checkouts/{checkout_token}/confirm", response_model=SubscriptionOut)
async def confirm_subscription_checkout(
    checkout_token: str,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> SubscriptionOut:
    row = (
        await db.execute(select(PaymentTransaction).where(PaymentTransaction.checkout_token == checkout_token))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Checkout not found")
    if row.student_user_id != actor.id and not _is_admin(actor):
        raise HTTPException(status_code=403, detail="Forbidden")

    student = (await db.execute(select(UserShadow).where(UserShadow.id == row.student_user_id))).scalar_one()
    teacher = (await db.execute(select(UserShadow).where(UserShadow.id == row.teacher_user_id))).scalar_one()

    transitioned_to_paid = False
    if row.status != "paid":
        if row.provider == "stripe":
            try:
                stripe_session = await get_stripe_checkout_session(
                    secret_key=settings.stripe_secret_key,
                    session_id=row.checkout_token,
                )
            except PaymentProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if stripe_session.get("payment_status") != "paid":
                raise HTTPException(status_code=400, detail="Stripe payment is not completed yet")
        elif row.provider == "paypal":
            try:
                paypal_payment = await capture_paypal_order(
                    client_id=settings.paypal_client_id,
                    client_secret=settings.paypal_client_secret,
                    env=settings.paypal_env,
                    order_id=row.provider_order_id or row.checkout_token,
                )
            except PaymentProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if paypal_payment.get("payment_status") != "paid":
                raise HTTPException(status_code=400, detail="PayPal payment is not completed yet")
            row.provider_order_id = row.provider_order_id or row.checkout_token
            row.provider_capture_id = str(paypal_payment.get("capture_id") or "") or row.provider_capture_id

        row.status = "paid"
        row.paid_at = _utc_now()
        row.teacher_earnings_cents, row.platform_fee_cents = _calc_teacher_earnings_cents(row.amount_cents)
        transitioned_to_paid = True
    elif row.amount_cents > 0 and row.teacher_earnings_cents == 0 and row.platform_fee_cents == 0:
        row.teacher_earnings_cents, row.platform_fee_cents = _calc_teacher_earnings_cents(row.amount_cents)

    if row.status == "paid":
        await _credit_teacher_wallet_from_payment(
            db,
            payment=row,
            teacher_wp_user_id=teacher.wp_user_id,
        )

    sub = await _create_subscription(
        db,
        teacher=teacher,
        student=student,
        months=row.months,
        sessions_per_month=row.sessions_per_month,
        charge_balance=False,
    )
    row.subscription_id = sub.id
    if transitioned_to_paid or row.status == "paid":
        await _record_wallet_ledger(
            db,
            student_user_id=student.id,
            direction="debit",
            amount_cents=max(int(row.amount_cents or 0), 0),
            points_delta=0,
            source="subscription_checkout",
            reference_type="payment_transaction",
            reference_id=str(row.id),
            note=f"Checkout payment to teacher #{teacher.wp_user_id}",
        )
    await db.commit()
    await db.refresh(row)
    await db.refresh(sub)
    return _to_subscription_out(sub, teacher, student)


@router.get("/payments/transactions", response_model=list[PaymentTransactionOut])
async def list_my_transactions(
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[PaymentTransactionOut]:
    rows = (
        await db.execute(
            select(PaymentTransaction)
            .where(PaymentTransaction.student_user_id == actor.id)
            .order_by(desc(PaymentTransaction.created_at))
        )
    ).scalars().all()
    return [
        PaymentTransactionOut(
            id=row.id,
            checkout_token=row.checkout_token,
            amount_cents=row.amount_cents,
            currency=row.currency,
            provider=row.provider,
            status=row.status,
            provider_order_id=row.provider_order_id,
            provider_capture_id=row.provider_capture_id,
            teacher_earnings_cents=row.teacher_earnings_cents,
            platform_fee_cents=row.platform_fee_cents,
            paid_at=row.paid_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/students/{student_wp_user_id}/wallet/topup/checkout",
    response_model=WalletTopupCheckoutOut,
)
async def create_wallet_topup_checkout(
    student_wp_user_id: int,
    payload: WalletTopupCheckoutIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> WalletTopupCheckoutOut:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)
    if not (_is_student(student) or _is_admin(actor)):
        raise HTTPException(status_code=403, detail="Only students can top up wallet")

    selected_provider = (payload.provider or "paypal").strip().lower()
    provider = "mock"
    token = uuid.uuid4().hex
    success_url = payload.success_url or _default_payment_success_url()
    cancel_url = payload.cancel_url or _default_payment_cancel_url()
    checkout_url = f"{success_url}?checkout_token={token}"
    provider_order_id: str | None = None

    has_stripe = bool(settings.stripe_secret_key)
    has_paypal = bool(settings.paypal_client_id and settings.paypal_client_secret)
    if selected_provider not in {"auto", "mock", "stripe", "paypal"}:
        raise HTTPException(status_code=400, detail="provider must be auto, mock, stripe or paypal")
    if selected_provider == "stripe" and not has_stripe:
        raise HTTPException(status_code=400, detail="Stripe is not configured on server")
    if selected_provider == "paypal" and not has_paypal:
        raise HTTPException(status_code=400, detail="PayPal is not configured on server")

    if selected_provider == "auto":
        if has_paypal:
            provider = "paypal"
        elif has_stripe:
            provider = "stripe"
    elif selected_provider in {"stripe", "paypal"}:
        provider = selected_provider

    if provider == "stripe":
        try:
            stripe_session = await create_stripe_checkout_session(
                secret_key=settings.stripe_secret_key,
                amount_cents=payload.amount_cents,
                currency="EUR",
                title="HomeBook • Wallet top-up",
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"student_wp_id": str(student.wp_user_id), "kind": "wallet_topup"},
            )
            token = stripe_session["session_id"]
            checkout_url = stripe_session["checkout_url"] or checkout_url
        except PaymentProviderError as exc:
            logger.exception("Stripe wallet topup checkout creation failed")
            if selected_provider == "stripe":
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            provider = "mock"

    if provider == "paypal":
        try:
            paypal_order = await create_paypal_order(
                client_id=settings.paypal_client_id,
                client_secret=settings.paypal_client_secret,
                env=settings.paypal_env,
                amount_cents=payload.amount_cents,
                currency="EUR",
                title="HomeBook • Wallet top-up",
                return_url=success_url,
                cancel_url=cancel_url,
                custom_id=token,
            )
            token = paypal_order["order_id"]
            provider_order_id = paypal_order["order_id"]
            checkout_url = paypal_order["checkout_url"] or checkout_url
        except PaymentProviderError as exc:
            logger.exception("PayPal wallet topup order creation failed")
            if selected_provider == "paypal":
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            provider = "mock"

    row = WalletTopupTransaction(
        student_user_id=student.id,
        amount_cents=payload.amount_cents,
        currency="EUR",
        provider=provider,
        status="pending",
        checkout_token=token,
        checkout_url=checkout_url,
        provider_order_id=provider_order_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return WalletTopupCheckoutOut(
        checkout_token=row.checkout_token,
        checkout_url=row.checkout_url,
        amount_cents=row.amount_cents,
        currency=row.currency,
        provider=row.provider,
        status=row.status,
    )


@router.post(
    "/students/{student_wp_user_id}/wallet/topup/confirm",
    response_model=WalletTopupCheckoutOut,
)
async def confirm_wallet_topup_checkout(
    student_wp_user_id: int,
    payload: WalletTopupConfirmIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> WalletTopupCheckoutOut:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)
    tx = (
        await db.execute(
            select(WalletTopupTransaction).where(
                and_(
                    WalletTopupTransaction.student_user_id == student.id,
                    WalletTopupTransaction.checkout_token == payload.checkout_token,
                )
            )
        )
    ).scalar_one_or_none()
    if tx is None:
        raise HTTPException(status_code=404, detail="Wallet topup checkout not found")

    if tx.status != "paid":
        if tx.provider == "stripe":
            try:
                stripe_session = await get_stripe_checkout_session(
                    secret_key=settings.stripe_secret_key,
                    session_id=tx.checkout_token,
                )
            except PaymentProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if stripe_session.get("payment_status") != "paid":
                raise HTTPException(status_code=400, detail="Stripe payment is not completed yet")
        elif tx.provider == "paypal":
            try:
                paypal_payment = await capture_paypal_order(
                    client_id=settings.paypal_client_id,
                    client_secret=settings.paypal_client_secret,
                    env=settings.paypal_env,
                    order_id=tx.provider_order_id or tx.checkout_token,
                )
            except PaymentProviderError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if paypal_payment.get("payment_status") != "paid":
                raise HTTPException(status_code=400, detail="PayPal payment is not completed yet")
            tx.provider_order_id = tx.provider_order_id or tx.checkout_token
            tx.provider_capture_id = str(paypal_payment.get("capture_id") or "") or tx.provider_capture_id

        tx.status = "paid"
        tx.paid_at = _utc_now()

    balance = await _ensure_student_balance(db, student.id)
    existing_credit = (
        await db.execute(
            select(WalletLedger).where(
                and_(
                    WalletLedger.student_user_id == student.id,
                    WalletLedger.direction == "credit",
                    WalletLedger.source == "wallet_topup",
                    WalletLedger.reference_type == "wallet_topup_transaction",
                    WalletLedger.reference_id == str(tx.id),
                )
            )
        )
    ).scalar_one_or_none()
    if existing_credit is None:
        points = _points_from_cents(tx.amount_cents)
        balance.balance += points
        await _record_wallet_ledger(
            db,
            student_user_id=student.id,
            direction="credit",
            amount_cents=max(int(tx.amount_cents), 0),
            points_delta=points,
            source="wallet_topup",
            reference_type="wallet_topup_transaction",
            reference_id=str(tx.id),
            note=f"Wallet top-up ({points} pts credited)",
        )

    await db.commit()
    await db.refresh(tx)
    return WalletTopupCheckoutOut(
        checkout_token=tx.checkout_token,
        checkout_url=tx.checkout_url,
        amount_cents=tx.amount_cents,
        currency=tx.currency,
        provider=tx.provider,
        status=tx.status,
    )


@router.get(
    "/students/{student_wp_user_id}/wallet/topup/transactions",
    response_model=list[WalletTopupTransactionOut],
)
async def list_wallet_topup_transactions(
    student_wp_user_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[WalletTopupTransactionOut]:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)
    rows = (
        await db.execute(
            select(WalletTopupTransaction)
            .where(WalletTopupTransaction.student_user_id == student.id)
            .order_by(desc(WalletTopupTransaction.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [
        WalletTopupTransactionOut(
            id=row.id,
            checkout_token=row.checkout_token,
            amount_cents=row.amount_cents,
            currency=row.currency,
            provider=row.provider,
            status=row.status,
            provider_order_id=row.provider_order_id,
            provider_capture_id=row.provider_capture_id,
            paid_at=row.paid_at,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get(
    "/students/{student_wp_user_id}/wallet/ledger",
    response_model=list[WalletLedgerOut],
)
async def list_wallet_ledger(
    student_wp_user_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> list[WalletLedgerOut]:
    _ensure_self_or_admin(actor, student_wp_user_id)
    student = await _user_by_wp_id(db, student_wp_user_id)
    rows = (
        await db.execute(
            select(WalletLedger)
            .where(WalletLedger.student_user_id == student.id)
            .order_by(desc(WalletLedger.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return [
        WalletLedgerOut(
            id=row.id,
            direction=row.direction,
            amount_cents=row.amount_cents,
            points_delta=row.points_delta,
            source=row.source,
            reference_type=row.reference_type,
            reference_id=row.reference_id,
            note=row.note,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/sessions/{session_id}/access-token", response_model=SessionAccessTokenOut)
async def create_session_access_token(
    session_id: int,
    payload: SessionAccessTokenIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> SessionAccessTokenOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _ensure_session_access(db, actor=actor, row=row)

    token = uuid.uuid4().hex
    expires_at = _utc_now() + timedelta(seconds=int(payload.ttl_seconds))
    access_row = SessionAccessToken(
        session_id=row.id,
        token=token,
        created_by_user_id=actor.id,
        expires_at=expires_at,
    )
    db.add(access_row)
    await db.commit()
    await db.refresh(access_row)
    return SessionAccessTokenOut(
        session_id=row.id,
        token=token,
        expires_at=expires_at,
        access_url=_append_query(_session_access_url(row), "access_token", token),
    )


@router.get("/sessions/access-token/{token}", response_model=SessionAccessOut)
async def get_session_access_by_token(
    token: str,
    db: AsyncSession = Depends(get_db),
) -> SessionAccessOut:
    now = _utc_now()
    access = (
        await db.execute(
            select(SessionAccessToken).where(
                and_(
                    SessionAccessToken.token == token,
                    SessionAccessToken.expires_at >= now,
                )
            )
        )
    ).scalar_one_or_none()
    if access is None:
        raise HTTPException(status_code=404, detail="Access token not found or expired")

    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == access.session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.kind == "live":
        _refresh_live_status(row)
    if access.used_at is None:
        access.used_at = now
    await db.commit()
    await db.refresh(row)
    return SessionAccessOut(
        session_id=row.id,
        access_url=_append_query(_session_access_url(row), "access_token", token),
        status=row.status,
        kind=row.kind,
    )


@router.post("/sessions/{session_id}/presence", response_model=SessionPresenceOut)
async def record_session_presence(
    session_id: int,
    payload: SessionPresenceIn,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> SessionPresenceOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _ensure_session_access(db, actor=actor, row=row)

    event_at = _utc_now()
    presence = SessionPresence(
        session_id=row.id,
        user_id=actor.id,
        event=payload.event,
        event_at=event_at,
    )
    db.add(presence)
    await db.commit()
    teacher = (await db.execute(select(UserShadow).where(UserShadow.id == row.teacher_user_id))).scalar_one_or_none()
    student_wp_id = None
    if row.target_student_user_id is not None:
        student = (
            await db.execute(select(UserShadow).where(UserShadow.id == row.target_student_user_id))
        ).scalar_one_or_none()
        student_wp_id = student.wp_user_id if student else None
    actor_avatar_url = (
        await db.execute(select(Profile.avatar_url).where(Profile.user_id == actor.id))
    ).scalar_one_or_none()
    if teacher is not None:
        await _publish_session_event(
            row=row,
            event_type="session.presence",
            teacher_wp_user_id=teacher.wp_user_id,
            student_wp_user_id=student_wp_id,
            actor=actor,
            actor_avatar_url=(actor_avatar_url or "").strip() or None,
            extra={
                "presence_event": payload.event,
                "presence_at": event_at.isoformat(),
            },
        )
    return SessionPresenceOut(session_id=row.id, event=payload.event, event_at=event_at)


@router.get("/sessions/{session_id}/presence/online", response_model=SessionPresenceSnapshotOut)
async def list_session_presence_online(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> SessionPresenceSnapshotOut:
    row = (await db.execute(select(TeacherSession).where(TeacherSession.id == session_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    await _ensure_session_access(db, actor=actor, row=row)

    events = (
        await db.execute(
            select(SessionPresence, UserShadow, Profile)
            .join(UserShadow, UserShadow.id == SessionPresence.user_id)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(SessionPresence.session_id == row.id)
            .order_by(desc(SessionPresence.event_at), desc(SessionPresence.id))
        )
    ).all()

    latest_by_user: dict[int, tuple[SessionPresence, UserShadow, Profile | None]] = {}
    for presence, user, profile in events:
        if user.id in latest_by_user:
            continue
        latest_by_user[user.id] = (presence, user, profile)

    stale_before = _utc_now() - timedelta(seconds=SESSION_PRESENCE_STALE_SECONDS)
    users: list[SessionPresenceUserOut] = []
    for presence, user, profile in latest_by_user.values():
        if presence.event != "joined":
            continue
        if presence.event_at < stale_before:
            continue
        users.append(
            SessionPresenceUserOut(
                wp_user_id=user.wp_user_id,
                display_name=user.display_name,
                role_tag=_role_tag_from_roles(user.roles),
                avatar_url=(profile.avatar_url if profile else None),
                last_event_at=presence.event_at,
            )
        )

    users.sort(key=lambda x: (x.display_name or "").lower())
    return SessionPresenceSnapshotOut(
        session_id=row.id,
        online_count=len(users),
        users=users,
    )


@router.get("/admin/payments/export")
async def export_payments_csv(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    actor: UserShadow = Depends(get_request_user),
) -> StreamingResponse:
    if not _is_admin(actor):
        raise HTTPException(status_code=403, detail="Admin only")

    stmt = select(PaymentTransaction).order_by(PaymentTransaction.created_at.asc())
    if date_from:
        stmt = stmt.where(PaymentTransaction.created_at >= date_from)
    if date_to:
        stmt = stmt.where(PaymentTransaction.created_at <= date_to)
    payment_rows = (await db.execute(stmt)).scalars().all()

    topup_stmt = select(WalletTopupTransaction).order_by(WalletTopupTransaction.created_at.asc())
    if date_from:
        topup_stmt = topup_stmt.where(WalletTopupTransaction.created_at >= date_from)
    if date_to:
        topup_stmt = topup_stmt.where(WalletTopupTransaction.created_at <= date_to)
    topup_rows = (await db.execute(topup_stmt)).scalars().all()

    user_ids = set()
    for row in payment_rows:
        user_ids.add(row.student_user_id)
        user_ids.add(row.teacher_user_id)
    for row in topup_rows:
        user_ids.add(row.student_user_id)
    users = (await db.execute(select(UserShadow).where(UserShadow.id.in_(list(user_ids))))).scalars().all() if user_ids else []
    wp_by_user_id = {x.id: x.wp_user_id for x in users}

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "type",
            "id",
            "status",
            "provider",
            "amount_cents",
            "currency",
            "student_wp_user_id",
            "teacher_wp_user_id",
            "created_at",
            "paid_at",
            "provider_order_id",
            "provider_capture_id",
        ]
    )
    for row in payment_rows:
        writer.writerow(
            [
                "subscription_checkout",
                row.id,
                row.status,
                row.provider,
                row.amount_cents,
                row.currency,
                wp_by_user_id.get(row.student_user_id, ""),
                wp_by_user_id.get(row.teacher_user_id, ""),
                row.created_at.isoformat() if row.created_at else "",
                row.paid_at.isoformat() if row.paid_at else "",
                row.provider_order_id or "",
                row.provider_capture_id or "",
            ]
        )
    for row in topup_rows:
        writer.writerow(
            [
                "wallet_topup",
                row.id,
                row.status,
                row.provider,
                row.amount_cents,
                row.currency,
                wp_by_user_id.get(row.student_user_id, ""),
                "",
                row.created_at.isoformat() if row.created_at else "",
                row.paid_at.isoformat() if row.paid_at else "",
                row.provider_order_id or "",
                row.provider_capture_id or "",
            ]
        )

    csv_bytes = io.BytesIO(buffer.getvalue().encode("utf-8"))
    filename = f"payments_export_{_utc_now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(csv_bytes, media_type="text/csv; charset=utf-8", headers=headers)
