from __future__ import annotations

from datetime import datetime, timezone
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import as_iso, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.social import ContentReport, Post, PostComment, PostReaction
from app.models.user import Block, Friendship
from app.models.user import UserShadow
from app.schemas.common import MessageResponse
from app.schemas.post import CommentIn, CommentOut, PostCreate, PostOut, ReactionIn, ReportIn
from app.services.auth import AuthUser, get_current_user
from app.services.notifications import create_notification
from app.services.text import extract_hashtags, extract_mentions

router = APIRouter(prefix="/posts", tags=["posts"])
_HANDLE_SANITIZE_RE = re.compile(r"[^a-z0-9._-]+")


async def _friend_ids(db: AsyncSession, user_id: int) -> set[int]:
    rows = (
        await db.execute(
            select(Friendship).where(or_(Friendship.user_low_id == user_id, Friendship.user_high_id == user_id))
        )
    ).scalars().all()

    out: set[int] = set()
    for row in rows:
        out.add(row.user_high_id if row.user_low_id == user_id else row.user_low_id)
    return out


async def _blocked_user_ids(db: AsyncSession, user_id: int) -> set[int]:
    rows = (
        await db.execute(
            select(Block).where(or_(Block.blocker_user_id == user_id, Block.blocked_user_id == user_id))
        )
    ).scalars().all()
    out: set[int] = set()
    for row in rows:
        if row.blocker_user_id == user_id:
            out.add(row.blocked_user_id)
        else:
            out.add(row.blocker_user_id)
    return out


async def _is_blocked_pair(db: AsyncSession, a_user_id: int, b_user_id: int) -> bool:
    row = (
        await db.execute(
            select(Block).where(
                or_(
                    and_(Block.blocker_user_id == a_user_id, Block.blocked_user_id == b_user_id),
                    and_(Block.blocker_user_id == b_user_id, Block.blocked_user_id == a_user_id),
                )
            )
        )
    ).scalar_one_or_none()
    return row is not None


def _normalize_handle(raw: str) -> str:
    txt = unicodedata.normalize("NFKD", str(raw or "")).encode("ascii", "ignore").decode("ascii")
    txt = txt.strip().lower().replace(" ", "_")
    txt = _HANDLE_SANITIZE_RE.sub("", txt)
    txt = txt.strip("._-")
    return txt[:32]


def _user_handle_aliases(user: UserShadow) -> set[str]:
    aliases: set[str] = set()
    display = _normalize_handle(user.display_name)
    if display:
        aliases.add(display)
        aliases.add(display.replace("_", ""))
    email_local = _normalize_handle((user.email or "").split("@", 1)[0])
    if email_local:
        aliases.add(email_local)
    return {x for x in aliases if x}


async def _resolve_mentioned_users(db: AsyncSession, mention_handles: list[str]) -> dict[str, UserShadow]:
    wanted = {_normalize_handle(h) for h in mention_handles if _normalize_handle(h)}
    if not wanted:
        return {}

    rows = (await db.execute(select(UserShadow))).scalars().all()
    alias_map: dict[str, UserShadow] = {}
    for user in rows:
        for alias in _user_handle_aliases(user):
            alias_map.setdefault(alias, user)

    out: dict[str, UserShadow] = {}
    for handle in sorted(wanted):
        user = alias_map.get(handle)
        if user is not None:
            out[handle] = user
    return out


async def _post_out(db: AsyncSession, post: Post, viewer_user_id: int | None = None) -> PostOut:
    wp_id = (
        await db.execute(select(UserShadow.wp_user_id).where(UserShadow.id == post.author_user_id))
    ).scalar_one_or_none()

    reactions_count = (
        await db.execute(select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post.id))
    ).scalar_one()
    comments_count = (
        await db.execute(select(func.count()).select_from(PostComment).where(PostComment.post_id == post.id))
    ).scalar_one()

    my_reaction: str | None = None
    liked_by_me = False
    if viewer_user_id is not None:
        my_reaction = (
            await db.execute(
                select(PostReaction.reaction_type).where(
                    and_(PostReaction.post_id == post.id, PostReaction.user_id == viewer_user_id)
                )
            )
        ).scalar_one_or_none()
        liked_by_me = my_reaction is not None

    return PostOut(
        id=post.id,
        author_wp_user_id=wp_id or 0,
        content=post.content,
        asset_url=post.asset_url,
        hashtags=post.hashtags or [],
        mentions=[str(m).lower() for m in (post.mentions or [])],
        created_at=as_iso(post.created_at),
        reactions_count=reactions_count,
        comments_count=comments_count,
        liked_by_me=liked_by_me,
        my_reaction=my_reaction,
    )


@router.post("", response_model=PostOut)
async def create_post(
    payload: PostCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> PostOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Post content is required")

    hashtags = extract_hashtags(content)
    mention_handles = extract_mentions(content)
    mention_handles = [h for h in mention_handles if _normalize_handle(h)]
    resolved_mentions = await _resolve_mentioned_users(db, mention_handles)
    normalized_handles = sorted({_normalize_handle(h) for h in mention_handles if _normalize_handle(h)})

    post = Post(
        author_user_id=me.id,
        content=content,
        asset_url=payload.asset_url,
        hashtags=hashtags,
        mentions=normalized_handles,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)

    if resolved_mentions:
        for handle, user in resolved_mentions.items():
            if user.id == me.id:
                continue
            await create_notification(
                db,
                user_id=user.id,
                kind="mention",
                title="Vous avez été mentionné",
                body=f"{me.display_name} vous a mentionné dans un post",
                payload={"post_id": post.id, "mention": handle},
            )

    return await _post_out(db, post, viewer_user_id=me.id)


@router.get("/feed", response_model=list[PostOut])
async def get_feed(
    limit: int = Query(default=30, ge=1, le=100),
    cursor: int | None = Query(default=None, ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[PostOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    friends = await _friend_ids(db, me.id)
    blocked = await _blocked_user_ids(db, me.id)

    friend_and_self = (friends | {me.id}) - blocked
    if not friend_and_self:
        friend_and_self = {-1}

    feed_where = [Post.author_user_id.in_(friend_and_self)]
    if cursor is not None:
        feed_where.append(Post.id < cursor)
    recent = (
        await db.execute(
            select(Post)
            .where(and_(*feed_where))
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    trending_where = []
    if blocked:
        trending_where.append(~Post.author_user_id.in_(blocked))
    if cursor is not None:
        trending_where.append(Post.id < cursor)
    trending_stmt = select(Post).join(PostReaction, PostReaction.post_id == Post.id)
    if trending_where:
        trending_stmt = trending_stmt.where(and_(*trending_where))
    trending_stmt = trending_stmt.group_by(Post.id).order_by(
        func.count(PostReaction.id).desc(),
        Post.created_at.desc(),
    ).limit(max(5, limit // 3))
    trending = (await db.execute(trending_stmt)).scalars().all()

    combined: dict[int, Post] = {p.id: p for p in recent}
    for p in trending:
        combined.setdefault(p.id, p)

    posts = sorted(combined.values(), key=lambda p: p.created_at, reverse=True)[:limit]
    out: list[PostOut] = []
    for p in posts:
        out.append(await _post_out(db, p, viewer_user_id=me.id))
    return out


@router.post("/{post_id}/reactions", response_model=MessageResponse)
async def react_post(
    post_id: int,
    payload: ReactionIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if await _is_blocked_pair(db, me.id, post.author_user_id):
        raise HTTPException(status_code=403, detail="Blocked relationship")

    reaction_type = (payload.reaction_type or "like").strip().lower() or "like"
    existing = (
        await db.execute(
            select(PostReaction).where(and_(PostReaction.post_id == post_id, PostReaction.user_id == me.id))
        )
    ).scalar_one_or_none()

    prev_type: str | None = existing.reaction_type if existing is not None else None

    if existing is not None and prev_type == reaction_type:
        return MessageResponse(message="Reaction already saved")

    if existing is None:
        existing = PostReaction(post_id=post_id, user_id=me.id, reaction_type=reaction_type)
        db.add(existing)
    else:
        existing.reaction_type = reaction_type

    await db.commit()

    if post.author_user_id != me.id and prev_type != reaction_type:
        await create_notification(
            db,
            user_id=post.author_user_id,
            kind="post_reaction",
            title="Nouvelle réaction",
            body=f"{me.display_name} a réagi à votre post",
            payload={"post_id": post_id},
        )

    return MessageResponse(message="Reaction saved")


@router.get("/{post_id}/comments", response_model=list[CommentOut])
async def list_post_comments(
    post_id: int,
    limit: int = Query(default=30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[CommentOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if await _is_blocked_pair(db, me.id, post.author_user_id):
        raise HTTPException(status_code=403, detail="Blocked relationship")

    rows = (
        await db.execute(
            select(PostComment, UserShadow.wp_user_id, UserShadow.display_name)
            .join(UserShadow, UserShadow.id == PostComment.author_user_id)
            .where(PostComment.post_id == post_id)
            .order_by(PostComment.id.desc())
            .limit(limit)
        )
    ).all()

    out: list[CommentOut] = []
    for comment, author_wp_id, author_name in rows:
        out.append(
            CommentOut(
                id=comment.id,
                post_id=comment.post_id,
                author_wp_user_id=int(author_wp_id or 0),
                author_name=str(author_name or ""),
                content=comment.content,
                created_at=as_iso(comment.created_at),
            )
        )
    out.reverse()
    return out


@router.post("/{post_id}/comments", response_model=CommentOut)
async def comment_post(
    post_id: int,
    payload: CommentIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> CommentOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    post = (await db.execute(select(Post).where(Post.id == post_id))).scalar_one_or_none()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if await _is_blocked_pair(db, me.id, post.author_user_id):
        raise HTTPException(status_code=403, detail="Blocked relationship")

    text = payload.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Comment content required")

    row = PostComment(
        post_id=post_id,
        author_user_id=me.id,
        content=text,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    if post.author_user_id != me.id:
        await create_notification(
            db,
            user_id=post.author_user_id,
            kind="post_comment",
            title="Nouveau commentaire",
            body=f"{me.display_name} a commenté votre post",
            payload={"post_id": post_id, "comment_id": row.id},
        )

    return CommentOut(
        id=row.id,
        post_id=row.post_id,
        author_wp_user_id=me.wp_user_id,
        author_name=me.display_name,
        content=row.content,
        created_at=as_iso(row.created_at),
    )


@router.post("/reports", response_model=MessageResponse)
async def create_report(
    payload: ReportIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    if payload.target_type not in {"post", "comment", "user", "message"}:
        raise HTTPException(status_code=400, detail="Invalid target_type")
    if len(payload.reason.strip()) < 6:
        raise HTTPException(status_code=400, detail="Reason is too short")

    report = ContentReport(
        reporter_user_id=me.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        reason=payload.reason.strip(),
        status="open",
    )
    db.add(report)
    await db.commit()
    return MessageResponse(message="Report submitted")
