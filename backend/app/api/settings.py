"""Settings CRUD routes (worldview, character, outline, plot, foreshadow)."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import get_current_approved_user
from app.db.session import get_db
from app.models.novel import Novel
from app.models.setting import Setting, SettingType, OutlineType
from app.models.user import User
from app.schemas.content import (
    SettingCreate,
    SettingOut,
    SettingUpdate,
    SettingBatchCreate,
    SettingOutlineNode,
)

router = APIRouter(prefix="/api/novels/{novel_id}/settings", tags=["settings"])


async def _get_owned_novel(novel_id: int, user: User, db: AsyncSession) -> Novel:
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Novel not found")
    if novel.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return novel


@router.get("", response_model=List[SettingOut])
async def list_settings(
    novel_id: int,
    type_filter: SettingType | None = Query(None, alias="type"),
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """List all settings for a novel, optionally filtered by type."""
    await _get_owned_novel(novel_id, user, db)
    stmt = select(Setting).where(Setting.novel_id == novel_id)
    if type_filter:
        stmt = stmt.where(Setting.type == type_filter)
    stmt = stmt.order_by(Setting.type, Setting.created_at)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=SettingOut, status_code=status.HTTP_201_CREATED)
async def create_setting(
    novel_id: int,
    body: SettingCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new setting element."""
    await _get_owned_novel(novel_id, user, db)
    setting = Setting(novel_id=novel_id, **body.model_dump())
    db.add(setting)
    await db.flush()
    await db.refresh(setting)
    return setting


@router.get("/{setting_id}", response_model=SettingOut)
async def get_setting(
    novel_id: int,
    setting_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single setting element."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Setting).where(Setting.id == setting_id, Setting.novel_id == novel_id)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")
    return setting


@router.put("/{setting_id}", response_model=SettingOut)
async def update_setting(
    novel_id: int,
    setting_id: int,
    body: SettingUpdate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a setting element."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Setting).where(Setting.id == setting_id, Setting.novel_id == novel_id)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(setting, key, value)
    await db.flush()
    await db.refresh(setting)
    return setting


@router.delete("/{setting_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_setting(
    novel_id: int,
    setting_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a setting element."""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Setting).where(Setting.id == setting_id, Setting.novel_id == novel_id)
    )
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Setting not found")
    await db.delete(setting)
    return None


# ── 大纲树形结构 ──────────────────────────────────────────

def _build_outline_tree(settings: List[Setting]) -> List[SettingOutlineNode]:
    """将平铺的大纲数据构建为树形结构"""
    node_map: dict[int, SettingOutlineNode] = {}
    roots: List[SettingOutlineNode] = []

    for s in settings:
        node = SettingOutlineNode(
            id=s.id,
            title=s.title,
            outline_type=s.outline_type,
            content=s.content,
            order_index=s.order_index if hasattr(s, 'order_index') else 0,
            children=[],
        )
        node_map[s.id] = node

    for s in settings:
        node = node_map[s.id]
        if s.parent_id and s.parent_id in node_map:
            node_map[s.parent_id].children.append(node)
        else:
            roots.append(node)

    # 按 order_index 排序各层
    for node in node_map.values():
        node.children.sort(key=lambda x: x.order_index)
    roots.sort(key=lambda x: x.order_index)

    return roots


@router.get("/outline", response_model=List[SettingOutlineNode])
async def get_outline_tree(
    novel_id: int,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """获取大纲树形结构（卷→章→场景）"""
    await _get_owned_novel(novel_id, user, db)
    result = await db.execute(
        select(Setting)
        .where(Setting.novel_id == novel_id, Setting.type == SettingType.outline)
        .order_by(Setting.order_index)
    )
    settings = result.scalars().all()
    return _build_outline_tree(settings)


# ── 批量创建 ─────────────────────────────────────────────

@router.post("/batch", response_model=List[SettingOut], status_code=status.HTTP_201_CREATED)
async def batch_create_settings(
    novel_id: int,
    body: SettingBatchCreate,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """批量创建设定元素"""
    await _get_owned_novel(novel_id, user, db)
    created: List[Setting] = []
    for item in body.items:
        setting = Setting(novel_id=novel_id, **item.model_dump())
        db.add(setting)
        created.append(setting)
    await db.flush()
    for s in created:
        await db.refresh(s)
    return created
