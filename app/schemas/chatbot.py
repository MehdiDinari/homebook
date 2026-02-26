from __future__ import annotations

from pydantic import BaseModel, Field


class ChatbotSessionCreate(BaseModel):
    work_id: str


class ChatbotSessionOut(BaseModel):
    id: int
    work_id: str
    title: str
    created_at: str


class ChatbotMessageCreate(BaseModel):
    message: str


class ChatbotMessageOut(BaseModel):
    role: str
    content: str
    created_at: str


class ChatbotSourceOut(BaseModel):
    kind: str
    label: str
    url: str
    excerpt: str = ""


class ChatbotReplyOut(BaseModel):
    answer: str
    messages: list[ChatbotMessageOut]
    sources: list[ChatbotSourceOut] = Field(default_factory=list)


class ChatbotSearchResultOut(BaseModel):
    work_id: str
    title: str
    author: str = ""
    cover_url: str = ""
    language: str = ""
    year: int | None = None


class ChatbotSearchOut(BaseModel):
    results: list[ChatbotSearchResultOut]


class ChatbotHistoryOut(BaseModel):
    session_id: int
    work_id: str
    messages: list[ChatbotMessageOut]
    sources: list[ChatbotSourceOut] = Field(default_factory=list)


class ChatbotChatIn(BaseModel):
    work_id: str
    message: str


class ChatbotResetIn(BaseModel):
    work_id: str
