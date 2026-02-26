from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import as_iso, get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.catalog import BookCache
from app.models.chatbot import ChatbotExport, ChatbotMessage, ChatbotSession
from app.schemas.chatbot import (
    ChatbotChatIn,
    ChatbotHistoryOut,
    ChatbotMessageCreate,
    ChatbotMessageOut,
    ChatbotReplyOut,
    ChatbotResetIn,
    ChatbotSearchOut,
    ChatbotSearchResultOut,
    ChatbotSourceOut,
    ChatbotSessionCreate,
    ChatbotSessionOut,
)
from app.services.auth import AuthUser, get_current_user
from app.services.ollama import ask_ollama
from app.services.openlibrary import get_book as openlibrary_get_book
from app.services.openlibrary import search_books as openlibrary_search_books

router = APIRouter(prefix="/chatbot", tags=["chatbot"])
logger = logging.getLogger(__name__)


def _session_out(row: ChatbotSession) -> ChatbotSessionOut:
    return ChatbotSessionOut(
        id=row.id,
        work_id=row.work_id,
        title=row.title,
        created_at=as_iso(row.created_at),
    )


def _message_out(row: ChatbotMessage) -> ChatbotMessageOut:
    return ChatbotMessageOut(
        role=row.role,
        content=row.content,
        created_at=as_iso(row.created_at),
    )


def _search_out_from_book(row: BookCache) -> ChatbotSearchResultOut:
    return ChatbotSearchResultOut(
        work_id=row.work_id,
        title=row.title,
        author=row.author,
        cover_url=row.cover_url,
        language=row.language,
        year=row.year,
    )


def _search_out_from_payload(payload: dict) -> ChatbotSearchResultOut:
    return ChatbotSearchResultOut(
        work_id=str(payload.get("work_id") or payload.get("id") or ""),
        title=str(payload.get("title") or ""),
        author=str(payload.get("author") or ""),
        cover_url=str(payload.get("cover_url") or ""),
        language=str(payload.get("language") or ""),
        year=payload.get("year"),
    )


def _excerpt(text: str, limit: int = 240) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _book_sources(book: BookCache) -> list[ChatbotSourceOut]:
    sources: list[ChatbotSourceOut] = []
    work_url = f"https://openlibrary.org/works/{book.work_id}"
    sources.append(
        ChatbotSourceOut(
            kind="book-work",
            label="OpenLibrary - fiche livre",
            url=work_url,
            excerpt=_excerpt(book.description or ""),
        )
    )
    if book.author:
        author_q = book.author.split(",")[0].strip()
        if author_q:
            sources.append(
                ChatbotSourceOut(
                    kind="author-search",
                    label="OpenLibrary - auteur",
                    url=f"https://openlibrary.org/search/authors.json?q={author_q.replace(' ', '+')}",
                    excerpt=author_q,
                )
            )
    return sources


async def _upsert_book(db: AsyncSession, payload: dict) -> BookCache:
    work_id = str(payload.get("work_id") or payload.get("id") or "").strip()
    if not work_id:
        raise ValueError("work_id is required")

    existing = (await db.execute(select(BookCache).where(BookCache.work_id == work_id))).scalar_one_or_none()
    if existing is None:
        existing = BookCache(
            work_id=work_id,
            title=str(payload.get("title") or ""),
            author=str(payload.get("author") or ""),
            description=str(payload.get("description") or ""),
            cover_url=str(payload.get("cover_url") or ""),
            language=str(payload.get("language") or "fr"),
            categories=payload.get("categories") or [],
            tags=payload.get("tags") or [],
            year=payload.get("year"),
            rating=payload.get("rating"),
            ratings_count=int(payload.get("ratings_count") or 0),
            web_reader_link=payload.get("web_reader_link"),
            source_payload=payload.get("source_payload") or {},
        )
        db.add(existing)
    else:
        existing.title = str(payload.get("title") or existing.title)
        existing.author = str(payload.get("author") or existing.author)
        existing.description = str(payload.get("description") or existing.description)
        existing.cover_url = str(payload.get("cover_url") or existing.cover_url)
        existing.language = str(payload.get("language") or existing.language)
        existing.categories = payload.get("categories") or existing.categories or []
        existing.tags = payload.get("tags") or existing.tags or []
        existing.year = payload.get("year")
        existing.rating = payload.get("rating")
        existing.ratings_count = int(payload.get("ratings_count") or 0)
        existing.web_reader_link = payload.get("web_reader_link")
        existing.source_payload = payload.get("source_payload") or existing.source_payload or {}

    await db.commit()
    await db.refresh(existing)
    return existing


async def _hydrate_book_from_openlibrary(db: AsyncSession, row: BookCache) -> BookCache:
    payload = await openlibrary_get_book(row.work_id)
    if payload is None:
        return row

    changed = False

    if not (row.description or "").strip() and (payload.get("description") or "").strip():
        row.description = str(payload.get("description") or "").strip()
        changed = True
    if not (row.author or "").strip() and (payload.get("author") or "").strip():
        row.author = str(payload.get("author") or "").strip()
        changed = True
    if not (row.cover_url or "").strip() and (payload.get("cover_url") or "").strip():
        row.cover_url = str(payload.get("cover_url") or "").strip()
        changed = True
    if not row.categories and payload.get("categories"):
        row.categories = payload.get("categories") or []
        changed = True
    if not row.tags and payload.get("tags"):
        row.tags = payload.get("tags") or []
        changed = True
    if not (row.language or "").strip() and (payload.get("language") or "").strip():
        row.language = str(payload.get("language") or "").strip()
        changed = True
    if row.year is None and payload.get("year") is not None:
        row.year = payload.get("year")
        changed = True
    if payload.get("source_payload"):
        row.source_payload = payload.get("source_payload") or row.source_payload or {}
        changed = True

    if changed:
        await db.commit()
        await db.refresh(row)

    return row


async def _ensure_book(db: AsyncSession, work_id: str) -> BookCache:
    row = (await db.execute(select(BookCache).where(BookCache.work_id == work_id))).scalar_one_or_none()
    if row is not None:
        # Search payloads can be sparse; hydrate details on first chat use.
        if not (row.description or "").strip():
            try:
                row = await _hydrate_book_from_openlibrary(db, row)
            except Exception:
                logger.exception("Failed to hydrate book details for work_id=%s", work_id)
        return row

    data = await openlibrary_get_book(work_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Book not found")

    row = BookCache(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _latest_session_for_work(
    db: AsyncSession,
    *,
    user_id: int,
    work_id: str,
) -> ChatbotSession | None:
    return (
        await db.execute(
            select(ChatbotSession)
            .where(and_(ChatbotSession.user_id == user_id, ChatbotSession.work_id == work_id))
            .order_by(ChatbotSession.created_at.desc(), ChatbotSession.id.desc())
        )
    ).scalars().first()


async def _get_or_create_session(
    db: AsyncSession,
    *,
    user_id: int,
    work_id: str,
) -> ChatbotSession:
    row = await _latest_session_for_work(db, user_id=user_id, work_id=work_id)
    if row is not None:
        return row

    book = await _ensure_book(db, work_id)
    row = ChatbotSession(
        user_id=user_id,
        work_id=work_id,
        title=book.title[:255] if book.title else f"Session {work_id}",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _messages_for_session(db: AsyncSession, session_id: int) -> list[ChatbotMessage]:
    return (
        await db.execute(
            select(ChatbotMessage)
            .where(ChatbotMessage.session_id == session_id)
            .order_by(ChatbotMessage.created_at.asc())
        )
    ).scalars().all()


async def _prompt_history(
    db: AsyncSession,
    session_id: int,
    limit: int = 10,
) -> list[dict[str, str]]:
    rows = (
        await db.execute(
            select(ChatbotMessage)
            .where(ChatbotMessage.session_id == session_id)
            .order_by(desc(ChatbotMessage.created_at), desc(ChatbotMessage.id))
            .limit(limit)
        )
    ).scalars().all()
    rows = list(reversed(rows))
    out: list[dict[str, str]] = []
    for r in rows:
        role = "assistant" if r.role == "assistant" else "user"
        out.append({"role": role, "content": r.content})
    return out


async def _append_and_answer(
    db: AsyncSession,
    *,
    session_row: ChatbotSession,
    question: str,
) -> ChatbotReplyOut:
    message = question.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    book = await _ensure_book(db, session_row.work_id)

    user_msg = ChatbotMessage(session_id=session_row.id, role="user", content=message)
    db.add(user_msg)
    await db.flush()

    history = await _prompt_history(db, session_row.id, limit=12)
    answer = await ask_ollama(
        book_title=book.title,
        book_author=book.author,
        book_description=book.description,
        book_categories=book.categories,
        user_message=message,
        history=history,
    )

    assistant_msg = ChatbotMessage(session_id=session_row.id, role="assistant", content=answer)
    db.add(assistant_msg)
    await db.commit()

    rows = await _messages_for_session(db, session_row.id)
    return ChatbotReplyOut(
        answer=answer,
        messages=[_message_out(r) for r in rows],
        sources=_book_sources(book),
    )


@router.get("/search", response_model=ChatbotSearchOut)
async def search_books_for_chatbot(
    query: str = Query(..., min_length=1),
    limit: int = Query(default=10, ge=1, le=30),
    language: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> ChatbotSearchOut:
    query = query.strip()
    if not query:
        return ChatbotSearchOut(results=[])

    payloads = await openlibrary_search_books(query, limit=limit, language=language)
    for payload in payloads:
        try:
            await _upsert_book(db, payload)
        except Exception:
            logger.exception("OpenLibrary upsert failed for chatbot search")

    q_like = f"%{query.lower()}%"
    stmt = select(BookCache).where(
        func.lower(BookCache.title).like(q_like) | func.lower(BookCache.author).like(q_like)
    )
    if language:
        stmt = stmt.where(func.lower(BookCache.language).like(f"%{language.lower()}%"))
    stmt = stmt.order_by(BookCache.updated_at.desc()).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    if rows:
        return ChatbotSearchOut(results=[_search_out_from_book(r) for r in rows])
    return ChatbotSearchOut(results=[_search_out_from_payload(x) for x in payloads][:limit])


@router.get("/history", response_model=ChatbotHistoryOut)
async def get_history_for_work(
    work_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatbotHistoryOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    session_row = await _get_or_create_session(db, user_id=me.id, work_id=work_id)
    rows = await _messages_for_session(db, session_row.id)
    return ChatbotHistoryOut(
        session_id=session_row.id,
        work_id=session_row.work_id,
        messages=[_message_out(r) for r in rows],
        sources=_book_sources(await _ensure_book(db, session_row.work_id)),
    )


@router.post("/chat", response_model=ChatbotReplyOut)
async def chat_for_work(
    payload: ChatbotChatIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatbotReplyOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    session_row = await _get_or_create_session(db, user_id=me.id, work_id=payload.work_id)
    return await _append_and_answer(db, session_row=session_row, question=payload.message)


@router.post("/reset", response_model=ChatbotHistoryOut)
async def reset_work_history(
    payload: ChatbotResetIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatbotHistoryOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    session_row = await _get_or_create_session(db, user_id=me.id, work_id=payload.work_id)

    await db.execute(delete(ChatbotMessage).where(ChatbotMessage.session_id == session_row.id))
    await db.commit()

    return ChatbotHistoryOut(
        session_id=session_row.id,
        work_id=session_row.work_id,
        messages=[],
        sources=_book_sources(await _ensure_book(db, session_row.work_id)),
    )


@router.post("/sessions", response_model=ChatbotSessionOut)
async def create_session(
    payload: ChatbotSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatbotSessionOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    book = await _ensure_book(db, payload.work_id)

    row = ChatbotSession(
        user_id=me.id,
        work_id=payload.work_id,
        title=book.title[:255] if book.title else f"Session {payload.work_id}",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _session_out(row)


@router.get("/sessions", response_model=list[ChatbotSessionOut])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[ChatbotSessionOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    rows = (
        await db.execute(
            select(ChatbotSession)
            .where(ChatbotSession.user_id == me.id)
            .order_by(ChatbotSession.created_at.desc())
        )
    ).scalars().all()
    return [_session_out(r) for r in rows]


@router.get("/sessions/{session_id}/messages", response_model=list[ChatbotMessageOut])
async def list_session_messages(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[ChatbotMessageOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    session_row = (
        await db.execute(
            select(ChatbotSession).where(
                and_(ChatbotSession.id == session_id, ChatbotSession.user_id == me.id)
            )
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise HTTPException(status_code=404, detail="Chatbot session not found")

    rows = await _messages_for_session(db, session_id)
    return [_message_out(r) for r in rows]


@router.post("/sessions/{session_id}/messages", response_model=ChatbotReplyOut)
async def create_chatbot_message(
    session_id: int,
    payload: ChatbotMessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> ChatbotReplyOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    session_row = (
        await db.execute(
            select(ChatbotSession).where(
                and_(ChatbotSession.id == session_id, ChatbotSession.user_id == me.id)
            )
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise HTTPException(status_code=404, detail="Chatbot session not found")

    return await _append_and_answer(db, session_row=session_row, question=payload.message)


@router.get("/sessions/{session_id}/export.txt")
async def export_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> PlainTextResponse:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    session_row = (
        await db.execute(
            select(ChatbotSession).where(
                and_(ChatbotSession.id == session_id, ChatbotSession.user_id == me.id)
            )
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise HTTPException(status_code=404, detail="Chatbot session not found")

    messages = await _messages_for_session(db, session_id)

    lines = [f"HomeBook Chatbot Export - Session {session_row.id}", f"Book: {session_row.work_id}", ""]
    for m in messages:
        role = "USER" if m.role == "user" else "ASSISTANT"
        lines.append(f"[{as_iso(m.created_at)}] {role}: {m.content}")
    content = "\n".join(lines)

    existing_export = (
        await db.execute(select(ChatbotExport).where(ChatbotExport.session_id == session_id))
    ).scalar_one_or_none()
    if existing_export is None:
        db.add(ChatbotExport(session_id=session_id, export_text=content))
    else:
        existing_export.export_text = content
    await db.commit()

    return PlainTextResponse(content=content)
