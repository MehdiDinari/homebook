from __future__ import annotations

from pydantic import BaseModel, Field


class PostCreate(BaseModel):
    content: str
    asset_url: str | None = None


class PostOut(BaseModel):
    id: int
    author_wp_user_id: int
    content: str
    asset_url: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    created_at: str
    reactions_count: int = 0
    comments_count: int = 0
    liked_by_me: bool = False
    my_reaction: str | None = None


class ReactionIn(BaseModel):
    reaction_type: str = "like"


class CommentIn(BaseModel):
    content: str


class CommentOut(BaseModel):
    id: int
    post_id: int
    author_wp_user_id: int
    author_name: str | None = None
    content: str
    created_at: str


class ReportIn(BaseModel):
    target_type: str
    target_id: str
    reason: str
