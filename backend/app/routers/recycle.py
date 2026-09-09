"""废纸篓（P4-2）：删除的实体入档 30 天可恢复，过期清理。

支持类型（v1）：
- chapter：删除章节时把整章内容 JSON 入档
- character / setting / foreshadow：删除时把实体字段 JSON 入档

端点：
- GET  /api/recycle?novel_id=N              列表（按 deleted_at desc）
- POST /api/recycle/{id}/restore           恢复（按 kind 在对应小说下重建）
- DELETE /api/recycle/{id}                 彻底删除
- POST /api/recycle/cleanup                手动清理过期条目（一般不需要，lazy 自动）

辅助：
- archive_to_recycle(db, user, novel, kind, name, obj)：被 chapters/characters/settings/foreshadows 调
- list_recycle / restore_recycle / cleanup_expired：内部函数
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_current_user
from ..models import (
    Character,
    Chapter,
    Foreshadowing,
    Novel,
    RecycleBin,
    User,
    WorldviewEntry,
)

router = APIRouter(prefix="/api/recycle", tags=["recycle"])

RETENTION_DAYS = 30


async def archive_to_recycle(
    db: AsyncSession,
    user: User,
    novel_id: int | None,
    kind: str,
    name: str,
    payload: dict,
) -> None:
    """被删除前调用一次，把实体入档废纸篓。

    payload 用 json.dumps 存到 RecycleBin.payload（恢复时反序列化重建）。
    """
    now = datetime.now(timezone.utc)
    entry = RecycleBin(
        user_id=user.id,
        novel_id=novel_id,
        kind=kind,
        name=(name or "")[:200],
        payload=json.dumps(payload, ensure_ascii=False, default=str),
        deleted_at=now,
        expires_at=now + timedelta(days=RETENTION_DAYS),
    )
    db.add(entry)


class RecycleItemOut(BaseModel):
    id: int
    novel_id: int | None
    kind: str
    name: str
    deleted_at: str
    expires_at: str
    days_left: int
    payload_preview: str  # 截前 200 字符用于列表预览


class CleanupResult(BaseModel):
    removed: int


@router.get("", response_model=list[RecycleItemOut])
async def list_recycle(
    novel_id: int | None = Query(default=None),
    kind: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户的废纸篓（按 deleted_at 倒序）。

    novel_id 过滤：None = 所有小说；N = 某小说。
    kind 过滤：chapter / character / setting / foreshadow / None(全部)。
    """
    # lazy 清理过期
    await _cleanup_expired(db, user.id)
    q = select(RecycleBin).where(RecycleBin.user_id == user.id)
    if novel_id is not None:
        q = q.where(RecycleBin.novel_id == novel_id)
    if kind is not None:
        q = q.where(RecycleBin.kind == kind)
    q = q.order_by(RecycleBin.deleted_at.desc()).limit(200)
    rows = (await db.execute(q)).scalars().all()
    now = datetime.now(timezone.utc)
    out: list[RecycleItemOut] = []
    for r in rows:
        # 计算剩余天数
        delta = r.expires_at - now
        days_left = max(0, int(delta.total_seconds() // 86400))
        preview = (r.payload or "").replace("\n", " ")[:200]
        out.append(
            RecycleItemOut(
                id=r.id,
                novel_id=r.novel_id,
                kind=r.kind,
                name=r.name,
                deleted_at=r.deleted_at.isoformat(),
                expires_at=r.expires_at.isoformat(),
                days_left=days_left,
                payload_preview=preview,
            )
        )
    return out


@router.post("/{entry_id}/restore")
async def restore_entry(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """恢复一条废纸篓条目：按 kind 重建实体（章节/人物/设定/伏笔）。"""
    entry = await db.get(RecycleBin, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(404, "条目不存在")
    if entry.novel_id is None:
        raise HTTPException(400, "无归属小说，无法恢复")
    novel = await db.get(Novel, entry.novel_id)
    if novel is None or novel.owner_id != user.id:
        raise HTTPException(404, "归属小说已删除，无法恢复")

    try:
        data = json.loads(entry.payload)
    except json.JSONDecodeError:
        raise HTTPException(500, "存档已损坏")

    kind = entry.kind
    if kind == "chapter":
        new_ch = Chapter(
            novel_id=novel.id,
            title=data.get("title", entry.name or "未命名章节"),
            content=data.get("content", ""),
            word_count=data.get("word_count", 0),
            sort_order=data.get("sort_order", 0),
            volume_id=data.get("volume_id"),
            status=data.get("status", "draft"),
            tags=data.get("tags", "[]"),
        )
        db.add(new_ch)
    elif kind == "character":
        new_c = Character(
            novel_id=novel.id,
            name=data.get("name", entry.name or "未命名"),
            role=data.get("role", ""),
            tags=data.get("tags", ""),
            description=data.get("description", ""),
            relations=data.get("relations", "[]"),
        )
        db.add(new_c)
    elif kind == "setting":
        new_s = WorldviewEntry(
            novel_id=novel.id,
            category=data.get("category", ""),
            title=data.get("title", entry.name or "未命名"),
            content=data.get("content", ""),
        )
        db.add(new_s)
    elif kind == "foreshadow":
        new_f = Foreshadowing(
            novel_id=novel.id,
            title=data.get("title", entry.name or "未命名"),
            content=data.get("content", ""),
            status=data.get("status", "未回收"),
        )
        db.add(new_f)
    else:
        raise HTTPException(400, f"未知类型：{kind}")

    # 删除废纸篓条目
    await db.delete(entry)
    await db.commit()
    return {"ok": True, "kind": kind}


@router.post("/{entry_id}/restore_paragraph")
async def restore_paragraph(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """段落级恢复：返回被删的 paragraphs + chapter_id（前端负责追加到章节末尾）。

    v1 简化：不精确恢复原位置（需要 diff 算法），只追加到章节末尾。
    同时删除废纸篓条目。
    """
    entry = await db.get(RecycleBin, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(404, "条目不存在")
    if entry.kind != "paragraph":
        raise HTTPException(400, "非段落类型条目")
    try:
        data = json.loads(entry.payload)
    except json.JSONDecodeError:
        raise HTTPException(500, "存档已损坏")
    paragraphs = data.get("paragraphs", [])
    chapter_id = data.get("chapter_id")
    await db.delete(entry)
    await db.commit()
    return {
        "ok": True,
        "kind": "paragraph",
        "chapter_id": chapter_id,
        "chapter_title": data.get("chapter_title", ""),
        "paragraphs": paragraphs,
    }


@router.delete("/{entry_id}")
async def hard_delete_entry(
    entry_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """彻底删除一条废纸篓条目（不可恢复）。"""
    entry = await db.get(RecycleBin, entry_id)
    if entry is None or entry.user_id != user.id:
        raise HTTPException(404, "条目不存在")
    await db.delete(entry)
    await db.commit()
    return {"ok": True}


@router.post("/cleanup", response_model=CleanupResult)
async def cleanup_endpoint(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """清理当前用户所有过期条目（手动触发）。"""
    removed = await _cleanup_expired(db, user.id)
    return CleanupResult(removed=removed)


async def _cleanup_expired(db: AsyncSession, user_id: int) -> int:
    now = datetime.now(timezone.utc)
    rows = (
        await db.execute(
            select(RecycleBin).where(
                RecycleBin.user_id == user_id, RecycleBin.expires_at < now
            )
        )
    ).scalars().all()
    for r in rows:
        await db.delete(r)
    if rows:
        await db.commit()
    return len(rows)
