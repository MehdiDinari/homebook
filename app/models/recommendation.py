from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import TimestampMixin


class RecommendationScore(TimestampMixin, Base):
    __tablename__ = "recommendation_scores"
    __table_args__ = (UniqueConstraint("user_id", "work_id", name="uq_reco_user_work"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users_shadow.id", ondelete="CASCADE"), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey("books_cache.work_id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
