from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.redis import redis_client
from app.models.notification import Notification


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_notification(
    db: AsyncSession,
    *,
    user_id: int,
    kind: str,
    title: str,
    body: str,
    payload: dict | None = None,
) -> Notification:
    row = Notification(
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        payload=payload or {},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    msg = {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "payload": row.payload,
        "is_read": row.is_read,
        "created_at": _now_iso(),
    }
    try:
        await redis_client.publish(f"notif:{user_id}", json.dumps(msg))
    except Exception:
        pass

    return row
