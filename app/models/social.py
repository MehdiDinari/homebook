from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class Post(TimestampMixin, Base):
    __tablename__ = "posts"
    __table_args__ = (
        Index("ix_posts_author_created", "author_user_id", "created_at"),
        Index(
            "ix_posts_tsv",
            text("to_tsvector('simple', coalesce(content,''))"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    asset_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    mentions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class PostReaction(TimestampMixin, Base):
    __tablename__ = "post_reactions"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_reaction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    reaction_type: Mapped[str] = mapped_column(String(20), default="like", nullable=False)


class PostComment(TimestampMixin, Base):
    __tablename__ = "post_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class ContentReport(TimestampMixin, Base):
    __tablename__ = "content_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporter_user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)
