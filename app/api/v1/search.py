from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.catalog import BookCache
from app.models.chat import ChatMember, ChatRoom
from app.models.social import Post
from app.models.user import Block, PrivacySettings, Profile, UserShadow
from app.schemas.search import SearchResponse, SearchResultItem
from app.services.auth import AuthUser, get_current_user

router = APIRouter(prefix="/search", tags=["search"])


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


@router.get("", response_model=SearchResponse)
async def global_search(
    q: str = Query(..., min_length=1),
    types: str = Query(default="books,users,rooms,posts"),
    limit: int = Query(default=20, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> SearchResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    wanted = {t.strip().lower() for t in types.split(",") if t.strip()}
    q_like = f"%{q.lower()}%"
    blocks = (
        await db.execute(
            select(Block).where(or_(Block.blocker_user_id == me.id, Block.blocked_user_id == me.id))
        )
    ).scalars().all()
    blocked_ids: set[int] = set()
    for b in blocks:
        if b.blocker_user_id == me.id:
            blocked_ids.add(b.blocked_user_id)
        else:
            blocked_ids.add(b.blocker_user_id)

    out: list[SearchResultItem] = []

    if "books" in wanted:
        books = (
            await db.execute(
                select(BookCache)
                .where(
                    or_(
                        func.lower(BookCache.title).like(q_like),
                        func.lower(BookCache.author).like(q_like),
                        func.lower(BookCache.description).like(q_like),
                    )
                )
                .limit(limit)
            )
        ).scalars().all()
        for b in books:
            out.append(
                SearchResultItem(type="book", id=b.work_id, title=b.title, subtitle=(b.author or None))
            )

    if "users" in wanted:
        users_stmt = (
            select(UserShadow, Profile, PrivacySettings)
            .outerjoin(Profile, Profile.user_id == UserShadow.id)
            .outerjoin(PrivacySettings, PrivacySettings.user_id == UserShadow.id)
            .where(
                and_(
                    or_(
                        func.lower(UserShadow.display_name).like(q_like),
                        func.lower(UserShadow.email).like(q_like),
                    ),
                    or_(PrivacySettings.id.is_(None), PrivacySettings.searchable.is_(True)),
                )
            )
            .limit(limit)
        )
        if blocked_ids:
            users_stmt = users_stmt.where(~UserShadow.id.in_(blocked_ids))
        users = (await db.execute(users_stmt)).all()
        for u, profile, privacy in users:
            if u.id == me.id:
                continue
            subtitle = profile.location if profile else None
            out.append(
                SearchResultItem(
                    type="user",
                    id=str(u.wp_user_id),
                    title=u.display_name,
                    subtitle=subtitle,
                    role_tag=_role_tag(u.roles),
                    avatar_url=_pick_avatar_url((profile.avatar_url if profile else None), u.email),
                )
            )

    if "rooms" in wanted:
        rooms_stmt = (
            select(ChatRoom)
            .join(ChatMember, ChatMember.room_id == ChatRoom.id)
            .where(
                and_(
                    ChatMember.user_id == me.id,
                    or_(
                        func.lower(ChatRoom.title).like(q_like),
                        func.lower(func.coalesce(ChatRoom.book_work_id, "")).like(q_like),
                    ),
                )
            )
            .limit(limit)
        )
        rooms = (await db.execute(rooms_stmt)).scalars().all()
        for r in rooms:
            out.append(
                SearchResultItem(type="room", id=str(r.id), title=r.title, subtitle=r.room_type)
            )

    if "posts" in wanted:
        posts_stmt = select(Post).where(func.lower(Post.content).like(q_like))
        if blocked_ids:
            posts_stmt = posts_stmt.where(~Post.author_user_id.in_(blocked_ids))
        posts = (await db.execute(posts_stmt.limit(limit))).scalars().all()
        for p in posts:
            out.append(
                SearchResultItem(type="post", id=str(p.id), title=p.content[:80], subtitle=f"author:{p.author_user_id}")
            )

    return SearchResponse(query=q, items=out[:limit])
