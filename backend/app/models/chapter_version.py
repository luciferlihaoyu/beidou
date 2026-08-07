"""ChapterVersion ORM model — historical snapshots of chapter state."""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ChapterVersion(Base):
    __tablename__ = "chapter_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # DB column name is ``created_by`` (FK to users.id); the ORM attribute is
    # ``created_by_id`` so the ``created_by`` property below can expose the
    # creator's *username* as the API contract requires.
    created_by_id: Mapped[Optional[int]] = mapped_column(
        "created_by", ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    chapter = relationship("Chapter", back_populates="versions")
    creator = relationship("User")

    @property
    def created_by(self) -> Optional[str]:
        """Username of the snapshot creator, or None if unknown."""
        return self.creator.username if self.creator else None
