from __future__ import annotations

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class BookCache(TimestampMixin, Base):
    __tablename__ = "books_cache"
    __table_args__ = (
        Index("ix_books_cache_work_id", "work_id", unique=True),
        Index(
            "ix_books_cache_tsv",
            text("to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(author,'') || ' ' || coalesce(description,''))"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    author: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cover_url: Mapped[str] = mapped_column(String(1200), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(20), default="fr", nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    ratings_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    web_reader_link: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    source_payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class BookFavorite(TimestampMixin, Base):
    __tablename__ = "book_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "work_id", name="uq_favorite_user_work"),
        CheckConstraint("state in ('favorite','to_read')", name="ck_book_favorite_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("books_cache.work_id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="favorite", nullable=False)


class ReadingProgress(TimestampMixin, Base):
    __tablename__ = "reading_progress"
    __table_args__ = (UniqueConstraint("user_id", "work_id", name="uq_progress_user_work"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("books_cache.work_id", ondelete="CASCADE"), index=True)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_position: Mapped[str | None] = mapped_column(String(255), nullable=True)
