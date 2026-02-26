from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.social import ContentReport
from app.models.user import Block, UserShadow
from app.schemas.common import MessageResponse
from app.schemas.post import ReportIn
from app.services.auth import AuthUser, get_current_user
from app.models.common import utcnow

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("", response_model=MessageResponse)
async def create_report(
    payload: ReportIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    if payload.target_type not in {"post", "comment", "user", "message"}:
        raise HTTPException(status_code=400, detail="Invalid target_type")
    reason = payload.reason.strip()
    if len(reason) < 6:
        raise HTTPException(status_code=400, detail="Reason is too short")

    now = utcnow()
    cooldown_since = now - timedelta(seconds=30)
    recent = (
        await db.execute(
            select(ContentReport).where(
                and_(
                    ContentReport.reporter_user_id == me.id,
                    ContentReport.created_at >= cooldown_since,
                )
            )
        )
    ).scalars().first()
    if recent is not None:
        raise HTTPException(status_code=429, detail="Please wait before sending another report")

    dup_since = now - timedelta(minutes=10)
    duplicate = (
        await db.execute(
            select(ContentReport).where(
                and_(
                    ContentReport.reporter_user_id == me.id,
                    ContentReport.target_type == payload.target_type,
                    ContentReport.target_id == payload.target_id,
                    ContentReport.created_at >= dup_since,
                )
            )
        )
    ).scalars().first()
    if duplicate is not None:
        return MessageResponse(message="Report already submitted recently")

    db.add(
        ContentReport(
            reporter_user_id=me.id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            reason=reason,
            status="open",
        )
    )
    await db.commit()
    return MessageResponse(message="Report submitted")


@router.post("/blocks/{target_wp_user_id}", response_model=MessageResponse)
async def block_user(
    target_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    if me.wp_user_id == target_wp_user_id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    target = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == target_wp_user_id))
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (
        await db.execute(
            select(Block).where(
                and_(Block.blocker_user_id == me.id, Block.blocked_user_id == target.id)
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(Block(blocker_user_id=me.id, blocked_user_id=target.id))
        await db.commit()
    return MessageResponse(message="User blocked")


@router.delete("/blocks/{target_wp_user_id}", response_model=MessageResponse)
async def unblock_user(
    target_wp_user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    target = (
        await db.execute(select(UserShadow).where(UserShadow.wp_user_id == target_wp_user_id))
    ).scalar_one_or_none()
    if target is None:
        return MessageResponse(message="User unblocked")
    row = (
        await db.execute(
            select(Block).where(
                and_(Block.blocker_user_id == me.id, Block.blocked_user_id == target.id)
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
    return MessageResponse(message="User unblocked")


@router.get("/blocks", response_model=list[int])
async def list_blocked_users(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[int]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    rows = (
        await db.execute(
            select(UserShadow.wp_user_id)
            .join(Block, Block.blocked_user_id == UserShadow.id)
            .where(Block.blocker_user_id == me.id)
            .order_by(UserShadow.wp_user_id.asc())
        )
    ).scalars().all()
    return [int(x) for x in rows]
