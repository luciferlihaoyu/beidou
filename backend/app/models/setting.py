"""Setting / world-building element ORM model."""

from datetime import datetime
from typing import Optional, List

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

import enum


class SettingType(str, enum.Enum):
    worldview = "worldview"
    character = "character"
    outline = "outline"
    plot = "plot"
    foreshadow = "foreshadow"


class OutlineType(str, enum.Enum):
    """大纲三级结构"""
    volume = "volume"
    chapter = "chapter"
    scene = "scene"


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id"), nullable=False, index=True)
    type: Mapped[SettingType] = mapped_column(
        Enum(SettingType), nullable=False, default=SettingType.worldview
    )
    # 大纲三级结构字段
    outline_type: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="volume/chapter/scene"
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("settings.id"), nullable=True, index=True,
        comment="自引用父节点"
    )
    order_index: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="排序"
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    novel = relationship("Novel", back_populates="settings")
    # 自引用：父节点
    parent: Mapped[Optional["Setting"]] = relationship(
        "Setting", remote_side="Setting.id", back_populates="children"
    )
    children: Mapped[List["Setting"]] = relationship(
        "Setting", back_populates="parent", cascade="all, delete-orphan",
        order_by="Setting.order_index"
    )
