from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HelpArticle(BaseModel):
    id: str
    title: str
    content: str


class SupportTicketCreateIn(BaseModel):
    subject: str = Field(min_length=4, max_length=180)
    priority: str = Field(default="normale", min_length=3, max_length=20)
    message: str = Field(min_length=12, max_length=6000)
    page: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default="help_support_form", max_length=80)


class SupportTicketStatusUpdateIn(BaseModel):
    status: str = Field(min_length=3, max_length=24)
    resolution_note: str | None = Field(default=None, max_length=6000)


class SupportTicketOut(BaseModel):
    id: int
    requester_wp_user_id: int
    requester_name: str
    requester_email: str
    subject: str
    priority: str
    status: str
    message: str
    source: str
    page_url: str | None = None
    resolution_note: str | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
