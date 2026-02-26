from __future__ import annotations

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    payload: dict = Field(default_factory=dict)
    is_read: bool
    created_at: str
