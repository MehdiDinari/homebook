from __future__ import annotations

from collections import Counter

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import BookCache, BookFavorite
from app.models.recommendation import RecommendationScore
from app.models.social import PostReaction


async def recompute_recommendations_for_user(db: AsyncSession, *, user_id: int, limit: int = 20) -> None:
    fav_stmt = (
        select(BookCache)
        .join(BookFavorite, BookFavorite.work_id == BookCache.work_id)
        .where(BookFavorite.user_id == user_id)
    )
    favorite_books = (await db.execute(fav_stmt)).scalars().all()

    tag_counter = Counter()
    for b in favorite_books:
        for t in b.tags or []:
            tag_counter[str(t).lower()] += 2
        for c in b.categories or []:
            tag_counter[str(c).lower()] += 1

    reacted_posts_count = (
        await db.execute(select(PostReaction).where(PostReaction.user_id == user_id))
    ).scalars().all()
    interaction_boost = min(len(reacted_posts_count), 20) * 0.05

    all_books = (await db.execute(select(BookCache))).scalars().all()

    scored: list[tuple[BookCache, float, str]] = []
    fav_work_ids = {b.work_id for b in favorite_books}

    for book in all_books:
        if book.work_id in fav_work_ids:
            continue
        overlap = 0.0
        for tag in book.tags or []:
            overlap += float(tag_counter.get(str(tag).lower(), 0))
        for cat in book.categories or []:
            overlap += float(tag_counter.get(str(cat).lower(), 0)) * 0.8

        if overlap <= 0:
            continue

        score = overlap + interaction_boost
        reason = "overlap_tags_categories"
        scored.append((book, score, reason))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:limit]

    await db.execute(delete(RecommendationScore).where(RecommendationScore.user_id == user_id))
    for book, score, reason in top:
        db.add(
            RecommendationScore(
                user_id=user_id,
                work_id=book.work_id,
                score=score,
                reason=reason,
            )
        )

    await db.commit()
