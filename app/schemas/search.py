from __future__ import annotations

from pydantic import BaseModel, Field


class SearchResultItem(BaseModel):
    type: str
    id: str
    title: str
    subtitle: str | None = None
    role_tag: str | None = None
    avatar_url: str | None = None


class SearchResponse(BaseModel):
    query: str
    items: list[SearchResultItem] = Field(default_factory=list)
