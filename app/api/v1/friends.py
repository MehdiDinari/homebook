from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import as_iso, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.user import FriendRequest, Friendship, Profile, UserShadow
from app.schemas.common import MessageResponse
from app.schemas.profile import (
    FriendRequestCreate,
    FriendRequestDetailedOut,
    FriendRequestOut,
    UserMiniOut,
)
from app.services.auth import AuthUser, get_current_user
from app.services.notifications import create_notification

router = APIRouter(prefix="/friends", tags=["friends"])


def _normalize_pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _role_tag(roles: list[str] | None) -> str | None:
    parsed = {str(x).strip().lower() for x in (roles or []) if str(x).strip()}
    if "administrator" in parsed:
        return "admin"
    if parsed.intersection({"prof", "teacher", "instructor"}):
        return "prof"
    if "student" in parsed:
        return "student"
    return None


def _to_user_mini(user: UserShadow, profile: Profile | None = None) -> UserMiniOut:
    email = str(user.email or "").strip().lower()
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest() if email else ""
    fallback_avatar = (
        f"https://secure.gravatar.com/avatar/{digest}?s=96&d=identicon&r=g"
        if digest
        else "https://secure.gravatar.com/avatar/?s=96&d=identicon&r=g"
    )
    avatar = (profile.avatar_url if profile and profile.avatar_url else fallback_avatar)
    return UserMiniOut(
        wp_user_id=user.wp_user_id,
        display_name=user.display_name,
        role_tag=_role_tag(user.roles),
        avatar_url=avatar,
    )


async def _are_friends(db: AsyncSession, user_a_id: int, user_b_id: int) -> bool:
    low, high = _normalize_pair(user_a_id, user_b_id)
    row = (
        await db.execute(
            select(Friendship).where(
                and_(Friendship.user_low_id == low, Friendship.user_high_id == high)
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def _friend_user_ids(db: AsyncSession, me_id: int) -> list[int]:
    rows = (
        await db.execute(
            select(Friendship).where(
                or_(Friendship.user_low_id == me_id, Friendship.user_high_id == me_id)
            )
        )
    ).scalars().all()
    out: list[int] = []
    for row in rows:
        out.append(row.user_high_id if row.user_low_id == me_id else row.user_low_id)
    return out


@router.post("/requests", response_model=FriendRequestOut)
async def create_friend_request(
    payload: FriendRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> FriendRequestOut:
    sender = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    recipient = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == payload.to_wp_user_id))
    ).scalar_one_or_none()
    if recipient is None:
        raise HTTPException(status_code=404, detail="Recipient not found")
    if sender.id == recipient.id:
        raise HTTPException(status_code=400, detail="Cannot friend yourself")
    if await _are_friends(db, sender.id, recipient.id):
        raise HTTPException(status_code=400, detail="Already friends")

    existing_reverse_pending = (
        await db.execute(
            select(FriendRequest).where(
                and_(
                    FriendRequest.from_user_id == recipient.id,
                    FriendRequest.to_user_id == sender.id,
                    FriendRequest.status == "pending",
                )
            )
        )
    ).scalar_one_or_none()
    if existing_reverse_pending is not None:
        raise HTTPException(status_code=409, detail="Incoming friend request pending: accept it")

    existing_pending = (
        await db.execute(
            select(FriendRequest).where(
                and_(
                    FriendRequest.from_user_id == sender.id,
                    FriendRequest.to_user_id == recipient.id,
                    FriendRequest.status == "pending",
                )
            )
        )
    ).scalar_one_or_none()
    if existing_pending:
        return FriendRequestOut(
            id=existing_pending.id,
            from_wp_user_id=sender.wp_user_id,
            to_wp_user_id=recipient.wp_user_id,
            status=existing_pending.status,
        )

    row = FriendRequest(from_user_id=sender.id, to_user_id=recipient.id, status="pending")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await create_notification(
        db,
        user_id=recipient.id,
        kind="friend_request",
        title="Nouvelle demande d'ami",
        body=f"{sender.display_name} vous a envoyé une demande d'ami",
        payload={"friend_request_id": row.id, "from_wp_user_id": sender.wp_user_id},
    )

    return FriendRequestOut(
        id=row.id,
        from_wp_user_id=sender.wp_user_id,
        to_wp_user_id=recipient.wp_user_id,
        status=row.status,
    )


@router.post("/requests/{request_id}/accept", response_model=MessageResponse)
async def accept_friend_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    req = (await db.execute(select(FriendRequest).where(FriendRequest.id == request_id))).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.to_user_id != me.id:
        raise HTTPException(status_code=403, detail="Not your request")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="Request already processed")

    req.status = "accepted"
    low, high = _normalize_pair(req.from_user_id, req.to_user_id)
    existing_friendship = (
        await db.execute(
            select(Friendship).where(
                and_(Friendship.user_low_id == low, Friendship.user_high_id == high)
            )
        )
    ).scalar_one_or_none()
    if existing_friendship is None:
        db.add(
            Friendship(
                user_low_id=low,
                user_high_id=high,
                created_at=datetime.now(timezone.utc),
            )
        )

    await db.commit()
    await create_notification(
        db,
        user_id=req.from_user_id,
        kind="friend_request_accepted",
        title="Demande d'ami acceptée",
        body=f"{me.display_name} a accepté votre demande d'ami",
        payload={"friend_request_id": req.id, "from_wp_user_id": me.wp_user_id},
    )
    return MessageResponse(message="Friend request accepted")


@router.post("/requests/{request_id}/decline", response_model=MessageResponse)
async def decline_friend_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    req = (await db.execute(select(FriendRequest).where(FriendRequest.id == request_id))).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.to_user_id != me.id:
        raise HTTPException(status_code=403, detail="Not your request")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="Request already processed")
    req.status = "declined"
    await db.commit()
    await create_notification(
        db,
        user_id=req.from_user_id,
        kind="friend_request_declined",
        title="Demande d'ami refusée",
        body=f"{me.display_name} a refusé votre demande d'ami",
        payload={"friend_request_id": req.id, "from_wp_user_id": me.wp_user_id},
    )
    return MessageResponse(message="Friend request declined")


@router.post("/requests/{request_id}/cancel", response_model=MessageResponse)
async def cancel_friend_request(
    request_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    req = (await db.execute(select(FriendRequest).where(FriendRequest.id == request_id))).scalar_one_or_none()
    if req is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.from_user_id != me.id:
        raise HTTPException(status_code=403, detail="Not your request")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail="Request already processed")
    req.status = "cancelled"
    await db.commit()
    return MessageResponse(message="Friend request cancelled")


@router.get("", response_model=list[UserMiniOut])
async def list_friends(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[UserMiniOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    friend_ids = await _friend_user_ids(db, me.id)
    if not friend_ids:
        return []
    rows = (
        await db.execute(
            select(UserShadow, Profile)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(UserShadow.id.in_(friend_ids))
            .order_by(UserShadow.display_name.asc())
        )
    ).all()
    return [_to_user_mini(user, profile) for user, profile in rows]


@router.get("/requests/incoming", response_model=list[FriendRequestDetailedOut])
async def list_incoming_friend_requests(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[FriendRequestDetailedOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    rows = (
        await db.execute(
            select(FriendRequest)
            .where(
                and_(
                    FriendRequest.to_user_id == me.id,
                    FriendRequest.status == "pending",
                )
            )
            .order_by(FriendRequest.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return []
    user_ids = {x.from_user_id for x in rows} | {x.to_user_id for x in rows}
    users = (
        await db.execute(
            select(UserShadow, Profile)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(UserShadow.id.in_(user_ids))
        )
    ).all()
    user_map: dict[int, tuple[UserShadow, Profile | None]] = {u.id: (u, p) for u, p in users}
    out: list[FriendRequestDetailedOut] = []
    for req in rows:
        from_u, from_p = user_map[req.from_user_id]
        to_u, to_p = user_map[req.to_user_id]
        out.append(
            FriendRequestDetailedOut(
                id=req.id,
                from_user=_to_user_mini(from_u, from_p),
                to_user=_to_user_mini(to_u, to_p),
                status=req.status,
                created_at=as_iso(req.created_at),
            )
        )
    return out


@router.get("/requests/outgoing", response_model=list[FriendRequestDetailedOut])
async def list_outgoing_friend_requests(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[FriendRequestDetailedOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    rows = (
        await db.execute(
            select(FriendRequest)
            .where(
                and_(
                    FriendRequest.from_user_id == me.id,
                    FriendRequest.status == "pending",
                )
            )
            .order_by(FriendRequest.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return []
    user_ids = {x.from_user_id for x in rows} | {x.to_user_id for x in rows}
    users = (
        await db.execute(
            select(UserShadow, Profile)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(UserShadow.id.in_(user_ids))
        )
    ).all()
    user_map: dict[int, tuple[UserShadow, Profile | None]] = {u.id: (u, p) for u, p in users}
    out: list[FriendRequestDetailedOut] = []
    for req in rows:
        from_u, from_p = user_map[req.from_user_id]
        to_u, to_p = user_map[req.to_user_id]
        out.append(
            FriendRequestDetailedOut(
                id=req.id,
                from_user=_to_user_mini(from_u, from_p),
                to_user=_to_user_mini(to_u, to_p),
                status=req.status,
                created_at=as_iso(req.created_at),
            )
        )
    return out


@router.delete("/{friend_wp_user_id}", response_model=MessageResponse)
async def delete_friendship(
    friend_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    other = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == friend_wp_user_id))
    ).scalar_one_or_none()
    if other is None:
        raise HTTPException(status_code=404, detail="User not found")

    low, high = _normalize_pair(me.id, other.id)
    friendship = (
        await db.execute(
            select(Friendship).where(
                and_(Friendship.user_low_id == low, Friendship.user_high_id == high)
            )
        )
    ).scalar_one_or_none()

    if friendship is not None:
        await db.delete(friendship)

    reqs = (
        await db.execute(
            select(FriendRequest).where(
                or_(
                    and_(FriendRequest.from_user_id == me.id, FriendRequest.to_user_id == other.id),
                    and_(FriendRequest.from_user_id == other.id, FriendRequest.to_user_id == me.id),
                )
            )
        )
    ).scalars().all()
    for req in reqs:
        await db.delete(req)

    await db.commit()
    return MessageResponse(message="Friendship removed")
