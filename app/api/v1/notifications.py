from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import as_iso, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.notification import Notification, NotificationRead
from app.schemas.notification import NotificationOut
from app.schemas.common import MessageResponse
from app.services.auth import AuthUser, get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[NotificationOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    rows = (
        await db.execute(
            select(Notification)
            .where(Notification.user_id == me.id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    return [
        NotificationOut(
            id=n.id,
            kind=n.kind,
            title=n.title,
            body=n.body,
            payload=n.payload or {},
            is_read=n.is_read,
            created_at=as_iso(n.created_at),
        )
        for n in rows
    ]


@router.post("/{notification_id}/read", response_model=MessageResponse)
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    row = (
        await db.execute(
            select(Notification).where(and_(Notification.id == notification_id, Notification.user_id == me.id))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")

    row.is_read = True
    existing = (
        await db.execute(
            select(NotificationRead).where(
                and_(
                    NotificationRead.notification_id == row.id,
                    NotificationRead.user_id == me.id,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            NotificationRead(
                notification_id=row.id,
                user_id=me.id,
                read_at=datetime.now(timezone.utc),
            )
        )

    await db.commit()
    return MessageResponse(message="Notification marked as read")


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_notifications_read(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    rows = (
        await db.execute(
            select(Notification).where(and_(Notification.user_id == me.id, Notification.is_read.is_(False)))
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for row in rows:
        row.is_read = True
        existing = (
            await db.execute(
                select(NotificationRead).where(
                    and_(
                        NotificationRead.notification_id == row.id,
                        NotificationRead.user_id == me.id,
                    )
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(NotificationRead(notification_id=row.id, user_id=me.id, read_at=now))
    await db.commit()
    return MessageResponse(message="All notifications marked as read")
