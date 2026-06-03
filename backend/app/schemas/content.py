"""Novel + Chapter + Setting Pydantic schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.setting import SettingType, OutlineType


# ── Novel ────────────────────────────────────────────────

class NovelCreate(BaseModel):
    """Request body to create a novel."""

    title: str = Field(..., min_length=1, max_length=255)
    synopsis: Optional[str] = None
    genre: Optional[str] = Field(None, max_length=128)
    tags: Optional[str] = Field(None, max_length=512)
    cover_url: Optional[str] = Field(None, max_length=1024)


class NovelUpdate(BaseModel):
    """Request body to update a novel."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    synopsis: Optional[str] = None
    genre: Optional[str] = Field(None, max_length=128)
    tags: Optional[str] = Field(None, max_length=512)
    cover_url: Optional[str] = Field(None, max_length=1024)


class NovelOut(BaseModel):
    """Public novel representation."""

    id: int
    title: str
    synopsis: Optional[str] = None
    genre: Optional[str] = None
    tags: Optional[str] = None
    cover_url: Optional[str] = None
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Chapter ──────────────────────────────────────────────

class ChapterCreate(BaseModel):
    """Request body to create a chapter."""

    title: str = Field(..., min_length=1, max_length=255)
    content: Optional[str] = None
    order_index: Optional[int] = 0


class ChapterUpdate(BaseModel):
    """Request body to update a chapter."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    content: Optional[str] = None
    order_index: Optional[int] = None
    word_count: Optional[int] = None


class ChapterOut(BaseModel):
    """Public chapter representation."""

    id: int
    novel_id: int
    title: str
    content: Optional[str] = None
    order_index: int
    word_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChapterReorder(BaseModel):
    """Batch reorder: mapping of chapter id → new order_index."""

    order: dict[int, int]


# ── Setting ──────────────────────────────────────────────

class SettingCreate(BaseModel):
    """Request body to create a setting element."""

    type: SettingType
    title: str = Field(..., min_length=1, max_length=255)
    content: Optional[str] = None
    metadata_json: Optional[str] = None
    # 大纲字段
    outline_type: Optional[str] = Field(None, max_length=16, description="volume/chapter/scene")
    parent_id: Optional[int] = Field(None, description="父节点ID")


class SettingUpdate(BaseModel):
    """Request body to update a setting element."""

    type: Optional[SettingType] = None
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    content: Optional[str] = None
    metadata_json: Optional[str] = None
    outline_type: Optional[str] = Field(None, max_length=16)
    parent_id: Optional[int] = None
    order_index: Optional[int] = None


class SettingBatchCreate(BaseModel):
    """Batch create settings."""
    items: List[SettingCreate]


class SettingOut(BaseModel):
    """Public setting element representation."""

    id: int
    novel_id: int
    type: SettingType
    title: str
    content: Optional[str] = None
    metadata_json: Optional[str] = None
    outline_type: Optional[str] = None
    parent_id: Optional[int] = None
    order_index: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SettingOutlineNode(BaseModel):
    """大纲树节点"""
    id: int
    title: str
    outline_type: Optional[str] = None
    content: Optional[str] = None
    order_index: int = 0
    children: List["SettingOutlineNode"] = []

    model_config = {"from_attributes": True}
