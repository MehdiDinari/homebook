from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_user_shadow_by_wp_id
from app.db.session import get_db
from app.models.help import SupportTicket
from app.models.user import UserShadow
from app.schemas.help import (
    HelpArticle,
    SupportTicketCreateIn,
    SupportTicketOut,
    SupportTicketStatusUpdateIn,
)
from app.services.auth import AuthUser, get_current_user

router = APIRouter(prefix="/help", tags=["help"])


ARTICLES = [
    HelpArticle(
        id="safety-001",
        title="Signaler un contenu",
        content="Utilisez le bouton de signalement sur un post, un commentaire ou un message.",
    ),
    HelpArticle(
        id="privacy-001",
        title="Confidentialité du profil",
        content="Réglez la visibilité de votre profil depuis Paramètres > Confidentialité.",
    ),
    HelpArticle(
        id="chatbot-001",
        title="Chatbot livres",
        content="Sélectionnez un livre avant de poser une question. Les extraits protégés longs sont refusés.",
    ),
]


@router.get("/articles", response_model=list[HelpArticle])
async def list_help_articles() -> list[HelpArticle]:
    return ARTICLES


_TICKET_PRIORITY_ALIASES = {
    "low": "basse",
    "basse": "basse",
    "normal": "normale",
    "normale": "normale",
    "medium": "normale",
    "high": "haute",
    "haute": "haute",
    "critical": "critique",
    "critique": "critique",
}
_ALLOWED_PRIORITIES = {"basse", "normale", "haute", "critique"}
_TICKET_STATUS_ALIASES = {
    "open": "open",
    "opened": "open",
    "new": "open",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "in progress": "in_progress",
    "pending": "in_progress",
    "resolved": "resolved",
    "done": "resolved",
    "closed": "closed",
}
_ALLOWED_STATUSES = {"open", "in_progress", "resolved", "closed"}
_SUPPORT_AGENT_ROLES = {"administrator", "admin", "support"}


def _normalize_priority(raw: str | None) -> str:
    key = str(raw or "").strip().lower()
    norm = _TICKET_PRIORITY_ALIASES.get(key, key)
    if norm not in _ALLOWED_PRIORITIES:
        raise HTTPException(status_code=422, detail="Invalid ticket priority")
    return norm


def _normalize_status(raw: str | None) -> str:
    key = str(raw or "").strip().lower()
    norm = _TICKET_STATUS_ALIASES.get(key, key)
    if norm not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid ticket status")
    return norm


def _is_support_agent(user: AuthUser) -> bool:
    roles = {str(x).strip().lower() for x in (user.roles or []) if str(x).strip()}
    return bool(roles.intersection(_SUPPORT_AGENT_ROLES))


def _as_ticket_out(ticket: SupportTicket, requester: UserShadow) -> SupportTicketOut:
    return SupportTicketOut(
        id=ticket.id,
        requester_wp_user_id=requester.wp_user_id,
        requester_name=requester.display_name,
        requester_email=requester.email,
        subject=ticket.subject,
        priority=ticket.priority,
        status=ticket.status,
        message=ticket.message,
        source=ticket.source,
        page_url=ticket.page_url,
        resolution_note=ticket.resolution_note,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        resolved_at=ticket.resolved_at,
    )


async def _ticket_with_requester(db: AsyncSession, ticket_id: int) -> tuple[SupportTicket, UserShadow] | None:
    row = (
        await db.execute(
            select(SupportTicket, UserShadow)
            .join(UserShadow, UserShadow.id == SupportTicket.requester_user_id)
            .where(SupportTicket.id == ticket_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1]


@router.post("/tickets", response_model=SupportTicketOut, status_code=201)
async def create_support_ticket(
    payload: SupportTicketCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> SupportTicketOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)

    row = SupportTicket(
        requester_user_id=me.id,
        subject=payload.subject.strip(),
        priority=_normalize_priority(payload.priority),
        status="open",
        message=payload.message.strip(),
        source=(payload.source or "help_support_form").strip()[:80] or "help_support_form",
        page_url=(payload.page or "").strip()[:1000] or None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _as_ticket_out(row, me)


@router.get("/tickets", response_model=list[SupportTicketOut])
async def list_support_tickets(
    limit: int = Query(default=20, ge=1, le=200),
    all_tickets: bool = Query(default=False, alias="all"),
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> list[SupportTicketOut]:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    can_list_all = _is_support_agent(current_user) and all_tickets

    stmt = (
        select(SupportTicket, UserShadow)
        .join(UserShadow, UserShadow.id == SupportTicket.requester_user_id)
        .order_by(desc(SupportTicket.created_at))
        .limit(limit)
    )
    if not can_list_all:
        stmt = stmt.where(SupportTicket.requester_user_id == me.id)

    rows = (await db.execute(stmt)).all()
    return [_as_ticket_out(ticket, requester) for ticket, requester in rows]


@router.get("/tickets/{ticket_id}", response_model=SupportTicketOut)
async def get_support_ticket(
    ticket_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> SupportTicketOut:
    me = await get_user_shadow_by_wp_id(db, current_user.wp_user_id)
    row = await _ticket_with_requester(db, ticket_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    ticket, requester = row
    if not _is_support_agent(current_user) and ticket.requester_user_id != me.id:
        raise HTTPException(status_code=404, detail="Ticket not found")

    return _as_ticket_out(ticket, requester)


@router.patch("/tickets/{ticket_id}/status", response_model=SupportTicketOut)
async def update_support_ticket_status(
    ticket_id: int,
    payload: SupportTicketStatusUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: AuthUser = Depends(get_current_user),
) -> SupportTicketOut:
    if not _is_support_agent(current_user):
        raise HTTPException(status_code=403, detail="Forbidden")

    row = (
        await db.execute(select(SupportTicket).where(SupportTicket.id == ticket_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found")

    status = _normalize_status(payload.status)
    row.status = status
    row.resolution_note = (payload.resolution_note or "").strip() or None
    if status in {"resolved", "closed"}:
        row.resolved_at = datetime.now(timezone.utc)
    elif status in {"open", "in_progress"}:
        row.resolved_at = None

    await db.commit()

    with_requester = await _ticket_with_requester(db, ticket_id)
    if with_requester is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket, requester = with_requester
    return _as_ticket_out(ticket, requester)
