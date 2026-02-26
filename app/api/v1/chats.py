from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import as_iso, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.chat import ChatMember, ChatMessage, ChatMessageRead, ChatRoom, ChatRoomInvite
from app.models.education import TeacherStudentSubscription
from app.models.user import Block, Friendship, Profile, UserShadow
from app.schemas.common import MessageResponse
from app.schemas.chat import (
    ChatInviteCreateIn,
    ChatInviteOut,
    ChatMemberPreview,
    ChatMessageCreate,
    ChatMessageOut,
    ChatPrivateRoomEnsureIn,
    ChatRoomCreate,
    ChatRoomOut,
)
from app.services.auth import AuthUser, get_current_user
from app.services.notifications import create_notification
from app.services.ws import ws_manager

router = APIRouter(prefix="/chats", tags=["chats"])

STUDENT_GROUP_MAX_MEMBERS = 5
TEACHER_GROUP_MAX_MEMBERS = 12
ADMIN_GROUP_MAX_MEMBERS = 30


def _fallback_avatar_url(email: str | None) -> str:
    normalized = (email or "").strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""
    if digest:
        return f"https://secure.gravatar.com/avatar/{digest}?s=96&d=identicon&r=g"
    return "https://secure.gravatar.com/avatar/?s=96&d=identicon&r=g"


def _pick_avatar_url(avatar_url: str | None, email: str | None) -> str:
    value = (avatar_url or "").strip()
    if value:
        return value
    return _fallback_avatar_url(email)


def _role_tag(roles: list[str] | None) -> str | None:
    parsed = {str(x).strip().lower() for x in (roles or []) if str(x).strip()}
    if "administrator" in parsed:
        return "admin"
    if parsed.intersection({"prof", "teacher", "instructor"}):
        return "prof"
    if "student" in parsed:
        return "student"
    return None


def _roles_set(user: UserShadow) -> set[str]:
    return {str(x).strip().lower() for x in (user.roles or []) if str(x).strip()}


def _is_admin(user: UserShadow) -> bool:
    return "administrator" in _roles_set(user)


def _is_teacher(user: UserShadow) -> bool:
    return bool(_roles_set(user).intersection({"prof", "teacher", "instructor"}))


def _is_student(user: UserShadow) -> bool:
    return "student" in _roles_set(user)


def _normalize_pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


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


async def _has_active_subscription(
    db: AsyncSession,
    *,
    teacher_user_id: int,
    student_user_id: int,
) -> bool:
    row = (
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
    return row is not None


async def _can_private_chat(db: AsyncSession, user_a: UserShadow, user_b: UserShadow) -> bool:
    if _is_admin(user_a) or _is_admin(user_b):
        return True
    if await _are_friends(db, user_a.id, user_b.id):
        return True
    if _is_teacher(user_a) and _is_student(user_b):
        return await _has_active_subscription(db, teacher_user_id=user_a.id, student_user_id=user_b.id)
    if _is_teacher(user_b) and _is_student(user_a):
        return await _has_active_subscription(db, teacher_user_id=user_b.id, student_user_id=user_a.id)
    return False


async def _can_group_invite(db: AsyncSession, inviter: UserShadow, invitee: UserShadow) -> bool:
    if _is_admin(inviter):
        return True
    if _is_teacher(inviter):
        return True
    if await _are_friends(db, inviter.id, invitee.id):
        return True
    return False


def _group_member_limit(owner: UserShadow) -> int:
    if _is_admin(owner):
        return ADMIN_GROUP_MAX_MEMBERS
    if _is_teacher(owner):
        return TEACHER_GROUP_MAX_MEMBERS
    return STUDENT_GROUP_MAX_MEMBERS


def _group_invite_error_detail(inviter: UserShadow) -> str:
    if _is_student(inviter):
        return "Students can invite only friends"
    return "Invite not allowed"


async def _room_member_entry(db: AsyncSession, room_id: int, user_id: int) -> ChatMember | None:
    return (
        await db.execute(
            select(ChatMember).where(and_(ChatMember.room_id == room_id, ChatMember.user_id == user_id))
        )
    ).scalar_one_or_none()


async def _room_member_count(db: AsyncSession, room_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(ChatMember).where(ChatMember.room_id == room_id)
            )
        ).scalar_one()
        or 0
    )


async def _room_pending_invites_count(db: AsyncSession, room_id: int) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(ChatRoomInvite)
                .where(and_(ChatRoomInvite.room_id == room_id, ChatRoomInvite.status == "pending"))
            )
        ).scalar_one()
        or 0
    )


async def _assert_room_member(db: AsyncSession, room_id: int, user_id: int) -> None:
    row = (
        await db.execute(
            select(ChatMember).where(and_(ChatMember.room_id == room_id, ChatMember.user_id == user_id))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=403, detail="Not a room member")


async def _assert_room_owner(db: AsyncSession, room_id: int, user_id: int) -> None:
    row = (
        await db.execute(
            select(ChatMember).where(
                and_(
                    ChatMember.room_id == room_id,
                    ChatMember.user_id == user_id,
                    ChatMember.member_role == "owner",
                )
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=403, detail="Only room owner can invite members")


async def _room_members_wp_ids(db: AsyncSession, room_id: int) -> list[int]:
    rows = (
        await db.execute(
            select(UserShadow.wp_user_id)
            .join(ChatMember, ChatMember.user_id == UserShadow.id)
            .where(ChatMember.room_id == room_id)
        )
    ).scalars().all()
    return list(rows)


async def _room_member_profiles(db: AsyncSession, room_id: int) -> list[ChatMemberPreview]:
    rows = (
        await db.execute(
            select(UserShadow, Profile)
            .join(ChatMember, ChatMember.user_id == UserShadow.id)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(ChatMember.room_id == room_id)
            .order_by(ChatMember.created_at.asc())
        )
    ).all()
    out: list[ChatMemberPreview] = []
    for user, profile in rows:
        out.append(
            ChatMemberPreview(
                wp_user_id=user.wp_user_id,
                display_name=user.display_name,
                role_tag=_role_tag(user.roles),
                avatar_url=_pick_avatar_url((profile.avatar_url if profile else None), user.email),
            )
        )
    return out


async def _is_blocked_pair(db: AsyncSession, a_user_id: int, b_user_id: int) -> bool:
    row = (
        await db.execute(
            select(Block).where(
                (
                    (Block.blocker_user_id == a_user_id)
                    & (Block.blocked_user_id == b_user_id)
                )
                | (
                    (Block.blocker_user_id == b_user_id)
                    & (Block.blocked_user_id == a_user_id)
                )
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def _room_unread_count(db: AsyncSession, room_id: int, user_id: int) -> int:
    unread_ids = (
        await db.execute(
            select(ChatMessage.id)
            .where(ChatMessage.room_id == room_id)
            .where(ChatMessage.sender_user_id != user_id)
            .where(
                ~ChatMessage.id.in_(
                    select(ChatMessageRead.message_id).where(ChatMessageRead.user_id == user_id)
                )
            )
        )
    ).scalars().all()
    return len(unread_ids)


async def _chat_room_out(db: AsyncSession, room: ChatRoom, me_user_id: int) -> ChatRoomOut:
    return ChatRoomOut(
        room_id=room.id,
        room_type=room.room_type,
        title=room.title,
        book_work_id=room.book_work_id,
        member_wp_user_ids=await _room_members_wp_ids(db, room.id),
        member_profiles=await _room_member_profiles(db, room.id),
        unread_count=await _room_unread_count(db, room.id, me_user_id),
        pending_invites_count=await _room_pending_invites_count(db, room.id),
    )


async def _chat_invite_out(db: AsyncSession, invite: ChatRoomInvite) -> ChatInviteOut:
    room = (await db.execute(select(ChatRoom).where(ChatRoom.id == invite.room_id))).scalar_one_or_none()

    user_rows = (
        await db.execute(
            select(UserShadow, Profile)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(UserShadow.id.in_([invite.inviter_user_id, invite.invitee_user_id]))
        )
    ).all()
    user_map: dict[int, tuple[UserShadow, Profile | None]] = {u.id: (u, p) for u, p in user_rows}
    inviter_u, _ = user_map.get(invite.inviter_user_id, (None, None))
    invitee_u, invitee_p = user_map.get(invite.invitee_user_id, (None, None))

    return ChatInviteOut(
        id=invite.id,
        room_id=invite.room_id,
        room_title=(room.title if room else None),
        inviter_wp_user_id=(inviter_u.wp_user_id if inviter_u else 0),
        inviter_display_name=(inviter_u.display_name if inviter_u else None),
        invitee_wp_user_id=(invitee_u.wp_user_id if invitee_u else 0),
        invitee_display_name=(invitee_u.display_name if invitee_u else None),
        invitee_role_tag=_role_tag(invitee_u.roles if invitee_u else []),
        invitee_avatar_url=_pick_avatar_url(
            (invitee_p.avatar_url if invitee_p else None),
            (invitee_u.email if invitee_u else None),
        ),
        status=invite.status,
        message=invite.message,
        created_at=as_iso(invite.created_at),
        responded_at=as_iso(invite.responded_at) if invite.responded_at else None,
    )


async def _find_private_room_between(
    db: AsyncSession,
    *,
    user_a_id: int,
    user_b_id: int,
) -> ChatRoom | None:
    candidates = (
        await db.execute(
            select(ChatRoom)
            .join(ChatMember, ChatMember.room_id == ChatRoom.id)
            .where(and_(ChatRoom.room_type == "private", ChatMember.user_id == user_a_id))
            .order_by(ChatRoom.updated_at.desc())
        )
    ).scalars().all()

    expected = {int(user_a_id), int(user_b_id)}
    for room in candidates:
        members = (
            await db.execute(select(ChatMember.user_id).where(ChatMember.room_id == room.id))
        ).scalars().all()
        member_set = {int(x) for x in members}
        if len(member_set) == 2 and member_set == expected:
            return room
    return None


def _validate_asset_url(asset_url: str | None) -> str | None:
    if asset_url is None:
        return None
    value = asset_url.strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="asset_url must be an http(s) URL")
    lower = value.lower()
    allowed_ext = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    if not any(lower.endswith(ext) for ext in allowed_ext):
        raise HTTPException(status_code=400, detail="Only image asset URLs are allowed")
    return value


@router.post("/rooms", response_model=ChatRoomOut)
async def create_room(
    payload: ChatRoomCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatRoomOut:
    creator = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    room_type = payload.room_type.lower().strip()
    if room_type not in {"book", "private", "group"}:
        raise HTTPException(status_code=400, detail="room_type must be one of book|private|group")

    if room_type == "book" and not payload.book_work_id:
        raise HTTPException(status_code=400, detail="book_work_id is required for book rooms")

    raw_member_wp_ids = sorted(set(int(x) for x in payload.member_wp_user_ids if int(x) != creator.wp_user_id))
    members = []
    if raw_member_wp_ids:
        members = (
            await db.execute(select(UserShadow).where(UserShadow.wp_user_id.in_(raw_member_wp_ids)))
        ).scalars().all()
        if len(members) != len(raw_member_wp_ids):
            raise HTTPException(status_code=400, detail="Some members do not exist")

    if room_type == "private":
        if len(raw_member_wp_ids) != 1:
            raise HTTPException(status_code=400, detail="Private chat requires exactly one peer")
        peer = members[0]
        if await _is_blocked_pair(db, creator.id, peer.id):
            raise HTTPException(status_code=403, detail="Cannot create room with blocked user")
        if not await _can_private_chat(db, creator, peer):
            raise HTTPException(status_code=403, detail="Private chat allowed only between friends or active teacher/student pairs")

    if room_type == "group":
        title = (payload.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Group title is required")
        max_members = _group_member_limit(creator)
        if len(raw_member_wp_ids) + 1 > max_members:
            raise HTTPException(
                status_code=400,
                detail=f"Group size cannot exceed {max_members} users (including creator)",
            )
        for member in members:
            if await _is_blocked_pair(db, creator.id, member.id):
                raise HTTPException(status_code=403, detail="Cannot create group with blocked user")
            if not await _can_group_invite(db, creator, member):
                raise HTTPException(status_code=403, detail=_group_invite_error_detail(creator))

    room = ChatRoom(
        room_type=room_type,
        title=payload.title,
        created_by_user_id=creator.id,
        book_work_id=payload.book_work_id,
    )
    db.add(room)
    await db.flush()

    db.add(ChatMember(room_id=room.id, user_id=creator.id, member_role="owner"))

    if room_type == "private":
        peer = members[0]
        db.add(ChatMember(room_id=room.id, user_id=peer.id, member_role="member"))
    elif room_type == "group":
        for user in members:
            invite = ChatRoomInvite(
                room_id=room.id,
                inviter_user_id=creator.id,
                invitee_user_id=user.id,
                status="pending",
                message=None,
            )
            db.add(invite)
            await db.flush()
            await create_notification(
                db,
                user_id=user.id,
                kind="chat_group_invite",
                title="Invitation à un groupe",
                body=f"{creator.display_name} vous a invité au groupe « {room.title} »",
                payload={"room_id": room.id, "invite_id": invite.id},
            )

    await db.commit()
    await db.refresh(room)

    return await _chat_room_out(db, room, creator.id)


@router.post("/rooms/private/ensure", response_model=ChatRoomOut)
async def ensure_private_room(
    payload: ChatPrivateRoomEnsureIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatRoomOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    if payload.peer_wp_user_id == me.wp_user_id:
        raise HTTPException(status_code=400, detail="Cannot create private room with yourself")

    peer = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == payload.peer_wp_user_id))
    ).scalar_one_or_none()
    if peer is None:
        raise HTTPException(status_code=404, detail="Peer user not found")
    if await _is_blocked_pair(db, me.id, peer.id):
        raise HTTPException(status_code=403, detail="Cannot create room with blocked user")
    if not await _can_private_chat(db, me, peer):
        raise HTTPException(status_code=403, detail="Private chat allowed only between friends or active teacher/student pairs")

    existing = await _find_private_room_between(db, user_a_id=me.id, user_b_id=peer.id)
    if existing is not None:
        return await _chat_room_out(db, existing, me.id)

    title = (payload.title or "").strip() or f"Discussion {me.display_name} / {peer.display_name}"
    room = ChatRoom(
        room_type="private",
        title=title[:255],
        created_by_user_id=me.id,
    )
    db.add(room)
    await db.flush()
    db.add(ChatMember(room_id=room.id, user_id=me.id, member_role="owner"))
    db.add(ChatMember(room_id=room.id, user_id=peer.id, member_role="member"))
    await db.commit()
    await db.refresh(room)
    return await _chat_room_out(db, room, me.id)


@router.get("/rooms", response_model=list[ChatRoomOut])
async def list_rooms(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[ChatRoomOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    rooms = (
        await db.execute(
            select(ChatRoom)
            .join(ChatMember, ChatMember.room_id == ChatRoom.id)
            .where(ChatMember.user_id == me.id)
            .order_by(ChatRoom.updated_at.desc())
        )
    ).scalars().all()

    out: list[ChatRoomOut] = []
    for room in rooms:
        out.append(await _chat_room_out(db, room, me.id))
    return out


@router.post("/rooms/{room_id}/invites", response_model=ChatInviteOut)
async def create_group_invite(
    room_id: int,
    payload: ChatInviteCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatInviteOut:
    inviter = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    room = (await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.room_type != "group":
        raise HTTPException(status_code=400, detail="Invitations are supported only for group rooms")
    await _assert_room_owner(db, room_id, inviter.id)

    invitee = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == payload.invitee_wp_user_id))
    ).scalar_one_or_none()
    if invitee is None:
        raise HTTPException(status_code=404, detail="Invitee user not found")
    if invitee.id == inviter.id:
        raise HTTPException(status_code=400, detail="Cannot invite yourself")
    if await _is_blocked_pair(db, inviter.id, invitee.id):
        raise HTTPException(status_code=403, detail="Cannot invite blocked user")
    if not await _can_group_invite(db, inviter, invitee):
        raise HTTPException(status_code=403, detail=_group_invite_error_detail(inviter))

    already_member = await _room_member_entry(db, room_id, invitee.id)
    if already_member is not None:
        raise HTTPException(status_code=400, detail="User is already a group member")

    existing_pending = (
        await db.execute(
            select(ChatRoomInvite).where(
                and_(
                    ChatRoomInvite.room_id == room_id,
                    ChatRoomInvite.invitee_user_id == invitee.id,
                    ChatRoomInvite.status == "pending",
                )
            )
        )
    ).scalar_one_or_none()
    if existing_pending is not None:
        return await _chat_invite_out(db, existing_pending)

    max_members = _group_member_limit(inviter)
    total_after = (await _room_member_count(db, room_id)) + (await _room_pending_invites_count(db, room_id)) + 1
    if total_after > max_members:
        raise HTTPException(
            status_code=400,
            detail=f"Group size cannot exceed {max_members} users (including pending invites)",
        )

    invite = ChatRoomInvite(
        room_id=room_id,
        inviter_user_id=inviter.id,
        invitee_user_id=invitee.id,
        status="pending",
        message=(payload.message or "").strip() or None,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)

    await create_notification(
        db,
        user_id=invitee.id,
        kind="chat_group_invite",
        title="Invitation à un groupe",
        body=f"{inviter.display_name} vous a invité au groupe « {room.title} »",
        payload={"room_id": room.id, "invite_id": invite.id},
    )

    return await _chat_invite_out(db, invite)


@router.get("/invites", response_model=list[ChatInviteOut])
async def list_my_invites(
    status: str = Query(default="pending"),
    room_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=300),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[ChatInviteOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    q = (
        select(ChatRoomInvite)
        .where(
            or_(
                ChatRoomInvite.invitee_user_id == me.id,
                ChatRoomInvite.inviter_user_id == me.id,
            )
        )
        .order_by(ChatRoomInvite.created_at.desc())
        .limit(limit)
    )
    if status != "all":
        q = q.where(ChatRoomInvite.status == status)
    if room_id is not None:
        q = q.where(ChatRoomInvite.room_id == room_id)
    rows = (await db.execute(q)).scalars().all()
    out: list[ChatInviteOut] = []
    for row in rows:
        out.append(await _chat_invite_out(db, row))
    return out


@router.post("/invites/{invite_id}/accept", response_model=ChatRoomOut)
async def accept_group_invite(
    invite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatRoomOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    invite = (await db.execute(select(ChatRoomInvite).where(ChatRoomInvite.id == invite_id))).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.invitee_user_id != me.id:
        raise HTTPException(status_code=403, detail="Not your invite")
    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invite already processed")

    room = (await db.execute(select(ChatRoom).where(ChatRoom.id == invite.room_id))).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if room.room_type != "group":
        raise HTTPException(status_code=400, detail="Invite room is not a group")

    inviter = (await db.execute(select(UserShadow).where(UserShadow.id == invite.inviter_user_id))).scalar_one_or_none()
    if inviter is None:
        raise HTTPException(status_code=404, detail="Inviter not found")
    if await _is_blocked_pair(db, me.id, inviter.id):
        raise HTTPException(status_code=403, detail="Blocked relationship with inviter")

    member = await _room_member_entry(db, room.id, me.id)
    if member is None:
        max_members = _group_member_limit(inviter)
        if (await _room_member_count(db, room.id)) >= max_members:
            raise HTTPException(status_code=400, detail=f"Group is full (max {max_members} users)")
        db.add(ChatMember(room_id=room.id, user_id=me.id, member_role="member"))

    invite.status = "accepted"
    invite.responded_at = datetime.now(timezone.utc)
    await db.commit()

    await create_notification(
        db,
        user_id=inviter.id,
        kind="chat_group_invite_accepted",
        title="Invitation acceptée",
        body=f"{me.display_name} a rejoint le groupe « {room.title} »",
        payload={"room_id": room.id, "invite_id": invite.id},
    )

    members = (
        await db.execute(select(ChatMember.user_id).where(ChatMember.room_id == room.id))
    ).scalars().all()
    for member_user_id in members:
        if member_user_id in {me.id, inviter.id}:
            continue
        await create_notification(
            db,
            user_id=member_user_id,
            kind="chat_group_member_joined",
            title="Nouveau membre",
            body=f"{me.display_name} a rejoint le groupe « {room.title} »",
            payload={"room_id": room.id, "invite_id": invite.id},
        )

    return await _chat_room_out(db, room, me.id)


@router.post("/invites/{invite_id}/decline", response_model=MessageResponse)
async def decline_group_invite(
    invite_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    invite = (await db.execute(select(ChatRoomInvite).where(ChatRoomInvite.id == invite_id))).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.invitee_user_id != me.id:
        raise HTTPException(status_code=403, detail="Not your invite")
    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invite already processed")

    room = (await db.execute(select(ChatRoom).where(ChatRoom.id == invite.room_id))).scalar_one_or_none()
    invite.status = "declined"
    invite.responded_at = datetime.now(timezone.utc)
    await db.commit()

    if room is not None:
        await create_notification(
            db,
            user_id=invite.inviter_user_id,
            kind="chat_group_invite_declined",
            title="Invitation refusée",
            body=f"{me.display_name} a refusé l'invitation au groupe « {room.title} »",
            payload={"room_id": room.id, "invite_id": invite.id},
        )

    return MessageResponse(message="Invite declined")


@router.get("/rooms/{room_id}/messages", response_model=list[ChatMessageOut])
async def list_messages(
    room_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[ChatMessageOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    await _assert_room_member(db, room_id, me.id)

    rows = (
        await db.execute(
            select(ChatMessage)
            .where(ChatMessage.room_id == room_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    rows = list(reversed(rows))

    sender_ids = {r.sender_user_id for r in rows} if rows else {-1}
    sender_rows = (
        await db.execute(
            select(
                UserShadow.id,
                UserShadow.wp_user_id,
                UserShadow.display_name,
                UserShadow.roles,
                UserShadow.email,
                Profile.avatar_url,
            )
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .where(UserShadow.id.in_(sender_ids))
        )
    ).all()
    sender_meta = {
        int(row[0]): {
            "wp_user_id": int(row[1]),
            "display_name": str(row[2] or ""),
            "roles": list(row[3] or []),
            "email": str(row[4] or "").strip().lower(),
            "avatar_url": _pick_avatar_url((str(row[5]).strip() if row[5] else None), str(row[4] or "")),
        }
        for row in sender_rows
    }

    seen_reads = (
        await db.execute(
            select(ChatMessageRead.message_id).where(
                and_(
                    ChatMessageRead.user_id == me.id,
                    ChatMessageRead.message_id.in_([r.id for r in rows] if rows else [-1]),
                )
            )
        )
    ).scalars().all()
    seen_set = {int(x) for x in seen_reads}
    now = datetime.now(timezone.utc)
    for r in rows:
        if r.sender_user_id == me.id:
            continue
        if r.id in seen_set:
            continue
        db.add(ChatMessageRead(message_id=r.id, user_id=me.id, read_at=now))
    await db.commit()

    return [
        ChatMessageOut(
            id=r.id,
            room_id=r.room_id,
            sender_wp_user_id=(sender_meta.get(r.sender_user_id) or {}).get("wp_user_id", 0),
            sender_display_name=(sender_meta.get(r.sender_user_id) or {}).get("display_name"),
            sender_role_tag=_role_tag((sender_meta.get(r.sender_user_id) or {}).get("roles") or []),
            sender_avatar_url=(sender_meta.get(r.sender_user_id) or {}).get("avatar_url"),
            content=r.content,
            asset_url=r.asset_url,
            created_at=as_iso(r.created_at),
        )
        for r in rows
    ]


@router.post("/rooms/{room_id}/messages", response_model=ChatMessageOut)
async def create_message(
    room_id: int,
    payload: ChatMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatMessageOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    await _assert_room_member(db, room_id, me.id)

    text = payload.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(text) > 4000:
        raise HTTPException(status_code=400, detail="Message too long")
    asset_url = _validate_asset_url(payload.asset_url)

    members = (
        await db.execute(select(ChatMember.user_id).where(ChatMember.room_id == room_id))
    ).scalars().all()
    for member_user_id in members:
        if member_user_id == me.id:
            continue
        if await _is_blocked_pair(db, me.id, int(member_user_id)):
            raise HTTPException(status_code=403, detail="Blocked relationship in this room")

    msg = ChatMessage(
        room_id=room_id,
        sender_user_id=me.id,
        content=text,
        asset_url=asset_url,
        created_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    room = (await db.execute(select(ChatRoom).where(ChatRoom.id == room_id))).scalar_one_or_none()
    if room is not None:
        room.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(msg)

    sender_avatar_url = _pick_avatar_url(
        (
            await db.execute(select(Profile.avatar_url).where(Profile.user_id == me.id))
        ).scalar_one_or_none(),
        me.email,
    )

    event = {
        "type": "chat_message",
        "id": msg.id,
        "room_id": msg.room_id,
        "sender_wp_user_id": me.wp_user_id,
        "sender_display_name": me.display_name,
        "sender_role_tag": _role_tag(me.roles),
        "sender_avatar_url": sender_avatar_url,
        "content": msg.content,
        "asset_url": msg.asset_url,
        "created_at": as_iso(msg.created_at),
    }
    await ws_manager.publish(f"chat:room:{room_id}", event)

    for member_user_id in members:
        if member_user_id == me.id:
            continue
        await create_notification(
            db,
            user_id=member_user_id,
            kind="chat_message",
            title="Nouveau message",
            body=f"{me.display_name} a envoyé un message",
            payload={"room_id": room_id, "message_id": msg.id},
        )

    return ChatMessageOut(
        id=msg.id,
        room_id=msg.room_id,
        sender_wp_user_id=me.wp_user_id,
        sender_display_name=me.display_name,
        sender_role_tag=_role_tag(me.roles),
        sender_avatar_url=sender_avatar_url,
        content=msg.content,
        asset_url=msg.asset_url,
        created_at=as_iso(msg.created_at),
    )
