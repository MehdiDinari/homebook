from __future__ import annotations

from datetime import datetime, timedelta, timezone

from arq.cron import cron
from arq.connections import RedisSettings
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.education import TeacherSession
from app.models.user import UserShadow
from app.services.openlibrary import search_books
from app.services.recommendations import recompute_recommendations_for_user


async def sync_books_job(ctx, query: str) -> dict:
    from app.api.v1.catalog import _upsert_book

    rows = await search_books(query, limit=60)
    async with SessionLocal() as db:
        for row in rows:
            await _upsert_book(db, row)
    return {"synced": len(rows), "query": query}


async def recompute_all_recommendations_job(ctx) -> dict:
    total = 0
    async with SessionLocal() as db:
        users = (await db.execute(select(UserShadow.id))).scalars().all()
        for user_id in users:
            await recompute_recommendations_for_user(db, user_id=user_id)
            total += 1
    return {"users_recomputed": total}


async def sync_live_sessions_status_job(ctx) -> dict:
    now = datetime.now(timezone.utc)
    changed = 0
    deleted = 0
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(TeacherSession).where(
                    TeacherSession.kind == "live",
                    TeacherSession.status.in_(["scheduled", "live"]),
                )
            )
        ).scalars().all()
        for row in rows:
            starts_at = row.starts_at
            ends_at = starts_at + timedelta(minutes=max(int(row.duration_minutes or 60), 1))
            new_status = row.status
            if now >= ends_at:
                new_status = "ended"
            elif now + timedelta(minutes=10) >= starts_at:
                new_status = "live"
            else:
                new_status = "scheduled"
            if new_status != row.status:
                row.status = new_status
                changed += 1

        grace_minutes = max(int(settings.live_session_cleanup_minutes or 0), 0)
        cutoff = now - timedelta(minutes=grace_minutes)
        ended_rows = (
            await db.execute(
                select(TeacherSession).where(
                    TeacherSession.kind == "live",
                    TeacherSession.status == "ended",
                )
            )
        ).scalars().all()
        ids_to_delete = []
        for row in ended_rows:
            ends_at = row.starts_at + timedelta(minutes=max(int(row.duration_minutes or 60), 1))
            if ends_at <= cutoff:
                ids_to_delete.append(row.id)
        if ids_to_delete:
            await db.execute(delete(TeacherSession).where(TeacherSession.id.in_(ids_to_delete)))
            deleted = len(ids_to_delete)

        if changed or deleted:
            await db.commit()
    return {"sessions_updated": changed, "sessions_deleted": deleted}


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [sync_books_job, recompute_all_recommendations_job, sync_live_sessions_status_job]
    cron_jobs = [cron(sync_live_sessions_status_job, minute=set(range(0, 60, 2)))]
