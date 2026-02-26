from __future__ import annotations

from pydantic import BaseModel, Field


class BookOut(BaseModel):
    work_id: str
    title: str
    author: str
    description: str = ""
    cover_url: str = ""
    language: str = "fr"
    categories: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    year: int | None = None
    rating: float | None = None
    ratings_count: int = 0
    web_reader_link: str | None = None


class BookListOut(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    items: list[BookOut]


class ReadingProgressIn(BaseModel):
    progress_percent: float
    last_position: str | None = None


class ReadingProgressOut(BaseModel):
    work_id: str
    progress_percent: float
    last_position: str | None = None


class RecommendationOut(BaseModel):
    work_id: str
    score: float
    reason: str
