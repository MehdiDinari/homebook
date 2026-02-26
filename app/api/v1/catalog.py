from __future__ import annotations

import logging
from math import ceil

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import Text, and_, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.catalog import BookCache, BookFavorite, ReadingProgress
from app.models.recommendation import RecommendationScore
from app.schemas.catalog import (
    BookListOut,
    BookOut,
    ReadingProgressIn,
    ReadingProgressOut,
    RecommendationOut,
)
from app.schemas.common import MessageResponse
from app.services.auth import AuthUser, get_current_user
from app.services.openlibrary import get_book as openlibrary_get_book
from app.services.openlibrary import search_books as openlibrary_search_books
from app.services.recommendations import recompute_recommendations_for_user

router = APIRouter(prefix="/catalog", tags=["catalog"])
logger = logging.getLogger(__name__)


def _book_out(row: BookCache) -> BookOut:
    return BookOut(
        work_id=row.work_id,
        title=row.title,
        author=row.author,
        description=row.description,
        cover_url=row.cover_url,
        language=row.language,
        categories=row.categories or [],
        tags=row.tags or [],
        year=row.year,
        rating=row.rating,
        ratings_count=row.ratings_count,
        web_reader_link=row.web_reader_link,
    )


async def _upsert_book(db: AsyncSession, payload: dict) -> BookCache:
    existing = (
        await db.execute(select(BookCache).where(BookCache.work_id == payload["work_id"]))
    ).scalar_one_or_none()

    if existing is None:
        existing = BookCache(**payload)
        db.add(existing)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)

    await db.commit()
    await db.refresh(existing)
    return existing


async def _bulk_upsert_books(db: AsyncSession, payloads: list[dict]) -> int:
    if not payloads:
        return 0

    wanted_keys = {
        "work_id",
        "title",
        "author",
        "description",
        "cover_url",
        "language",
        "categories",
        "tags",
        "year",
        "rating",
        "ratings_count",
        "web_reader_link",
        "source_payload",
    }

    by_work_id: dict[str, dict] = {}
    for payload in payloads:
        work_id = str(payload.get("work_id") or "").strip()
        if not work_id:
            continue
        row = {k: payload.get(k) for k in wanted_keys if k in payload}
        row["work_id"] = work_id
        row["title"] = str(row.get("title") or "").strip() or work_id
        row["author"] = str(row.get("author") or "").strip()
        row["description"] = str(row.get("description") or "").strip()
        row["cover_url"] = str(row.get("cover_url") or "").strip()
        row["language"] = str(row.get("language") or "fr").strip() or "fr"
        raw_categories = row.get("categories")
        row["categories"] = list(raw_categories) if isinstance(raw_categories, (list, tuple)) else []
        raw_tags = row.get("tags")
        row["tags"] = list(raw_tags) if isinstance(raw_tags, (list, tuple)) else []
        row["ratings_count"] = int(row.get("ratings_count") or 0)
        row["source_payload"] = row.get("source_payload") if isinstance(row.get("source_payload"), dict) else {}
        by_work_id[work_id] = row

    rows = list(by_work_id.values())
    if not rows:
        return 0

    stmt = insert(BookCache).values(rows)
    set_map = {
        "title": stmt.excluded.title,
        "author": stmt.excluded.author,
        "description": stmt.excluded.description,
        "cover_url": stmt.excluded.cover_url,
        "language": stmt.excluded.language,
        "categories": stmt.excluded.categories,
        "tags": stmt.excluded.tags,
        "year": stmt.excluded.year,
        "rating": stmt.excluded.rating,
        "ratings_count": stmt.excluded.ratings_count,
        "web_reader_link": stmt.excluded.web_reader_link,
        "source_payload": stmt.excluded.source_payload,
        "updated_at": func.now(),
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[BookCache.work_id],
        set_=set_map,
    )
    await db.execute(stmt)
    await db.commit()
    return len(rows)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _favorite_state(value: str | None) -> str:
    state = _norm(value) or "favorite"
    if state not in {"favorite", "to_read"}:
        raise HTTPException(status_code=400, detail="state must be favorite or to_read")
    return state


def _lang_matches(book_lang: str | None, requested_lang: str | None) -> bool:
    if not requested_lang:
        return True

    aliases = {
        "fr": {"fr", "fra", "fre", "fr-fr", "fr_ca", "fr-ca"},
        "en": {"en", "eng", "en-us", "en_us", "en-gb", "en_gb"},
        "ar": {"ar", "ara", "ar-sa", "ar_sa", "ar-ma", "ar_ma"},
    }
    bl = _norm(book_lang)
    req = _norm(requested_lang)
    if not bl:
        return True
    allowed = aliases.get(req, {req})
    if bl in allowed:
        return True
    return any(alias in bl for alias in allowed)


def _matches_category(row: BookCache, category: str | None) -> bool:
    if not category:
        return True
    wanted = _norm(category)
    cats = [str(x).lower() for x in (row.categories or [])]
    return any(wanted in c for c in cats)


def _matches_tag(row: BookCache, tag: str | None) -> bool:
    if not tag:
        return True
    wanted = _norm(tag)
    tags = [str(x).lower() for x in (row.tags or [])]
    return any(wanted in t for t in tags)


def _language_aliases(requested_lang: str | None) -> set[str]:
    req = _norm(requested_lang)
    if not req:
        return set()
    aliases = {
        "fr": {"fr", "fra", "fre", "fr-fr", "fr_ca", "fr-ca"},
        "en": {"en", "eng", "en-us", "en_us", "en-gb", "en_gb"},
        "ar": {"ar", "ara", "ar-sa", "ar_sa", "ar-ma", "ar_ma"},
    }
    return aliases.get(req, {req})


def _build_catalog_filters(
    *,
    query: str,
    language: str | None,
    category: str | None,
    tag: str | None,
):
    filters = []

    if query:
        q_like = f"%{query.lower()}%"
        filters.append(
            or_(
                func.lower(BookCache.title).like(q_like),
                func.lower(BookCache.author).like(q_like),
                func.lower(BookCache.description).like(q_like),
            )
        )

    lang_aliases = _language_aliases(language)
    if lang_aliases:
        lang_expr = func.lower(func.coalesce(BookCache.language, ""))
        filters.append(or_(lang_expr == "", *[lang_expr.like(f"%{x}%") for x in sorted(lang_aliases)]))

    if category:
        filters.append(func.lower(cast(BookCache.categories, Text)).like(f"%{_norm(category)}%"))

    if tag:
        filters.append(func.lower(cast(BookCache.tags, Text)).like(f"%{_norm(tag)}%"))

    return filters


def _sort_stmt(stmt, *, sort: str):
    if sort == "title":
        return stmt.order_by(BookCache.title.asc())
    if sort == "author":
        return stmt.order_by(BookCache.author.asc(), BookCache.title.asc())
    if sort == "year":
        return stmt.order_by(BookCache.year.desc().nullslast())
    if sort == "rating":
        return stmt.order_by(BookCache.rating.desc().nullslast())
    return stmt.order_by(BookCache.updated_at.desc())


def _apply_local_filters(
    rows: list[BookCache],
    *,
    language: str | None,
    category: str | None,
    tag: str | None,
) -> list[BookCache]:
    out: list[BookCache] = []
    for row in rows:
        if not _lang_matches(row.language, language):
            continue
        if not _matches_category(row, category):
            continue
        if not _matches_tag(row, tag):
            continue
        out.append(row)
    return out


def _dedupe_local_rows(rows: list[BookCache]) -> list[BookCache]:
    seen_work_ids: set[str] = set()
    seen_text: set[tuple[str, str, str]] = set()
    out: list[BookCache] = []
    for row in rows:
        if row.work_id in seen_work_ids:
            continue
        title = _norm(row.title)
        author = _norm(row.author)
        language = _norm(row.language)
        key = (title, author, language)
        if title and author and key in seen_text:
            continue
        seen_work_ids.add(row.work_id)
        if title and author:
            seen_text.add(key)
        out.append(row)
    return out


@router.get("/books", response_model=BookListOut)
async def list_books(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    language: str | None = Query(default=None),
    sort: str = Query(default="relevance"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> BookListOut:
    query = (q or "").strip()
    per_call_limit = min(100, max(page_size * 3, 36))

    filters = _build_catalog_filters(
        query=query,
        language=language,
        category=category,
        tag=tag,
    )

    def _with_filters(stmt):
        return stmt.where(and_(*filters)) if filters else stmt

    total_stmt = _with_filters(select(func.count()).select_from(BookCache))
    total = int((await db.execute(total_stmt)).scalar_one() or 0)

    # Local-first: only hit OpenLibrary when query has no local match,
    # or when cache is empty and there is no query.
    should_try_remote = bool(query and total == 0)
    if not query and total == 0:
        should_try_remote = True

    if should_try_remote:
        try:
            remote_payloads: list[dict] = []
            if query:
                remote_payloads = await openlibrary_search_books(
                    query,
                    limit=per_call_limit,
                    page=1,
                    language=language,
                    category=category,
                    tag=tag,
                )
            else:
                remote_payloads = await openlibrary_search_books(
                    "bestseller",
                    limit=min(60, per_call_limit),
                    page=1,
                    language=language,
                    category=category,
                    tag=tag,
                )
            upserted = await _bulk_upsert_books(db, remote_payloads)
            if upserted:
                total = int((await db.execute(total_stmt)).scalar_one() or 0)
        except Exception:
            logger.exception("Remote catalog sync failed")

    total_pages = max(1, ceil(total / page_size))
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    stmt = _with_filters(select(BookCache))
    stmt = _sort_stmt(stmt, sort=sort).offset(offset).limit(page_size)
    paged_rows = (await db.execute(stmt)).scalars().all()
    paged_rows = _dedupe_local_rows(
        _apply_local_filters(
            paged_rows,
            language=language,
            category=category,
            tag=tag,
        )
    )

    return BookListOut(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        items=[_book_out(x) for x in paged_rows],
    )


@router.get("/books/{work_id}", response_model=BookOut)
async def get_book(work_id: str, db: AsyncSession = Depends(get_db)) -> BookOut:
    row = (await db.execute(select(BookCache).where(BookCache.work_id == work_id))).scalar_one_or_none()
    if row is None:
        payload = await openlibrary_get_book(work_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Book not found")
        row = await _upsert_book(db, payload)

    return _book_out(row)


@router.post("/favorites/{work_id}", response_model=MessageResponse)
async def add_favorite(
    work_id: str,
    state: str = Query(default="favorite"),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    fav_state = _favorite_state(state)

    book = (await db.execute(select(BookCache).where(BookCache.work_id == work_id))).scalar_one_or_none()
    if book is None:
        payload = await openlibrary_get_book(work_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Book not found")
        await _upsert_book(db, payload)

    existing = (
        await db.execute(
            select(BookFavorite).where(
                and_(BookFavorite.user_id == user.id, BookFavorite.work_id == work_id)
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(BookFavorite(user_id=user.id, work_id=work_id, state=fav_state))
    else:
        existing.state = fav_state
    await db.commit()

    await recompute_recommendations_for_user(db, user_id=user.id)
    return MessageResponse(message="Favorite state saved")


@router.get("/favorites", response_model=list[BookOut])
async def list_favorites(
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[BookOut]:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    state_filter = _favorite_state(state) if state is not None else None

    stmt = (
        select(BookCache)
        .join(BookFavorite, BookFavorite.work_id == BookCache.work_id)
        .where(BookFavorite.user_id == user.id)
        .order_by(BookFavorite.created_at.desc())
    )
    if state_filter:
        stmt = stmt.where(BookFavorite.state == state_filter)
    rows = (await db.execute(stmt)).scalars().all()
    return [_book_out(x) for x in rows]


@router.delete("/favorites/{work_id}", response_model=MessageResponse)
async def remove_favorite(
    work_id: str,
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    state_filter = _favorite_state(state) if state is not None else None
    stmt = delete(BookFavorite).where(and_(BookFavorite.user_id == user.id, BookFavorite.work_id == work_id))
    if state_filter:
        stmt = stmt.where(BookFavorite.state == state_filter)
    await db.execute(stmt)
    await db.commit()
    await recompute_recommendations_for_user(db, user_id=user.id)
    return MessageResponse(message="Favorite removed")


@router.delete("/favorites", response_model=MessageResponse)
async def clear_favorites(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    await db.execute(delete(BookFavorite).where(BookFavorite.user_id == user.id))
    await db.commit()
    await recompute_recommendations_for_user(db, user_id=user.id)
    return MessageResponse(message="Favorites cleared")


@router.put("/progress/{work_id}", response_model=ReadingProgressOut)
async def upsert_progress(
    work_id: str,
    payload: ReadingProgressIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ReadingProgressOut:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    if payload.progress_percent < 0 or payload.progress_percent > 100:
        raise HTTPException(status_code=400, detail="progress_percent must be between 0 and 100")

    row = (
        await db.execute(
            select(ReadingProgress).where(
                and_(ReadingProgress.user_id == user.id, ReadingProgress.work_id == work_id)
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = ReadingProgress(
            user_id=user.id,
            work_id=work_id,
            progress_percent=payload.progress_percent,
            last_position=payload.last_position,
        )
        db.add(row)
    else:
        row.progress_percent = payload.progress_percent
        row.last_position = payload.last_position

    await db.commit()
    await db.refresh(row)

    return ReadingProgressOut(
        work_id=row.work_id,
        progress_percent=row.progress_percent,
        last_position=row.last_position,
    )


@router.get("/progress/{work_id}", response_model=ReadingProgressOut)
async def get_progress(
    work_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ReadingProgressOut:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    row = (
        await db.execute(
            select(ReadingProgress).where(
                and_(ReadingProgress.user_id == user.id, ReadingProgress.work_id == work_id)
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return ReadingProgressOut(work_id=work_id, progress_percent=0.0, last_position=None)
    return ReadingProgressOut(
        work_id=row.work_id,
        progress_percent=row.progress_percent,
        last_position=row.last_position,
    )


@router.get("/recommendations", response_model=list[RecommendationOut])
async def get_recommendations(
    limit: int = Query(default=12, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[RecommendationOut]:
    user = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    await recompute_recommendations_for_user(db, user_id=user.id)

    rows = (
        await db.execute(
            select(RecommendationScore)
            .where(RecommendationScore.user_id == user.id)
            .order_by(RecommendationScore.score.desc())
            .limit(limit)
        )
    ).scalars().all()

    return [RecommendationOut(work_id=r.work_id, score=r.score, reason=r.reason) for r in rows]


@router.post("/sync/books", response_model=MessageResponse)
async def sync_books_from_search(
    query: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> MessageResponse:
    # Internal utility endpoint for admins/content managers.
    if "administrator" not in current_user.roles and "editor" not in current_user.roles:
        raise HTTPException(status_code=403, detail="Forbidden")

    rows = await openlibrary_search_books(query, limit=50)
    upserted = await _bulk_upsert_books(db, rows)
    return MessageResponse(message=f"Synced {upserted} books")
