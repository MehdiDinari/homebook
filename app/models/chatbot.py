from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class ChatbotSession(TimestampMixin, Base):
    __tablename__ = "chatbot_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("books_cache.work_id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)


class ChatbotMessage(TimestampMixin, Base):
    __tablename__ = "chatbot_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chatbot_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)


class ChatbotExport(TimestampMixin, Base):
    __tablename__ = "chatbot_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chatbot_sessions.id", ondelete="CASCADE"), unique=True)
    export_text: Mapped[str] = mapped_column(Text, nullable=False)
