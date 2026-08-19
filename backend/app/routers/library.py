"""资料库：公共库 + 小说专属库，目录树 + 文本条目 + 全文搜索 + AI 辅助整理。"""

import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_current_user, get_default_ai_config, get_owned_novel
from ..models import LibraryFolder, LibraryItem, Novel, User

router = APIRouter(prefix="/api/library", tags=["library"])


# ---------- 作用域校验 ----------

async def _check_scope(novel_id: int | None, user: User, db: AsyncSession) -> None:
    """novel_id 为 None 表示公共库（全用户共享）；否则校验作品归属。"""
    if novel_id is None:
        return
    await get_owned_novel(novel_id, user, db)


def _scope_cond(model, novel_id: int | None):
    return model.novel_id.is_(None) if novel_id is None else model.novel_id == novel_id


# ---------- 目录 ----------

class FolderIn(BaseModel):
    novel_id: int | None = None
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0


class FolderOut(BaseModel):
    id: int
    novel_id: int | None
    parent_id: int | None
    name: str
    sort_order: int

    model_config = {"from_attributes": True}


@router.get("/folders", response_model=list[FolderOut])
async def list_folders(
    novel_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_scope(novel_id, user, db)
    result = await db.execute(
        select(LibraryFolder)
        .where(_scope_cond(LibraryFolder, novel_id))
        .order_by(LibraryFolder.sort_order, LibraryFolder.id)
    )
    return result.scalars().all()


@router.post("/folders", response_model=FolderOut)
async def create_folder(data: FolderIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _check_scope(data.novel_id, user, db)
    if data.parent_id is not None:
        parent = await db.get(LibraryFolder, data.parent_id)
        if parent is None or parent.novel_id != data.novel_id:
            raise HTTPException(400, "父目录不存在")
    folder = LibraryFolder(**data.model_dump())
    db.add(folder)
    await db.commit()
    await db.refresh(folder)
    return folder


@router.put("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: int, data: FolderIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    folder = await db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "目录不存在")
    await _check_scope(folder.novel_id, user, db)
    if data.parent_id == folder.id:
        raise HTTPException(400, "不能把目录移到自己下面")
    folder.name = data.name
    folder.parent_id = data.parent_id
    folder.sort_order = data.sort_order
    await db.commit()
    await db.refresh(folder)
    return folder


@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    folder = await db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "目录不存在")
    await _check_scope(folder.novel_id, user, db)
    # 子目录一并删除（ON DELETE CASCADE），目录内条目移到未归档
    items = (
        await db.execute(select(LibraryItem).where(LibraryItem.folder_id == folder.id))
    ).scalars().all()
    for item in items:
        item.folder_id = None
    await db.delete(folder)
    await db.commit()
    return {"ok": True}


# ---------- 条目 ----------

class ItemIn(BaseModel):
    novel_id: int | None = None
    folder_id: int | None = None
    title: str = Field(min_length=1, max_length=200)
    content: str = ""
    tags: str = Field(default="", max_length=300)
    summary: str = Field(default="", max_length=500)
    source: str = Field(default="manual", max_length=20)


class ItemOut(ItemIn):
    id: int
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _item_out(item: LibraryItem) -> dict:
    return {
        "id": item.id,
        "novel_id": item.novel_id,
        "folder_id": item.folder_id,
        "title": item.title,
        "content": item.content,
        "tags": item.tags,
        "summary": item.summary,
        "source": item.source,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


@router.get("/items")
async def list_items(
    novel_id: int | None = Query(default=None),
    folder_id: int | None = Query(default=None),
    unfiled: bool = Query(default=False),
    q: str = Query(default="", max_length=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _check_scope(novel_id, user, db)
    stmt = select(LibraryItem).where(_scope_cond(LibraryItem, novel_id))
    if unfiled:
        stmt = stmt.where(LibraryItem.folder_id.is_(None))
    elif folder_id is not None:
        # 包含子目录下的条目
        folders = (
            await db.execute(select(LibraryFolder).where(_scope_cond(LibraryFolder, novel_id)))
        ).scalars().all()
        ids = {folder_id}
        changed = True
        while changed:
            changed = False
            for f in folders:
                if f.parent_id in ids and f.id not in ids:
                    ids.add(f.id)
                    changed = True
        stmt = stmt.where(LibraryItem.folder_id.in_(ids))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(LibraryItem.title.like(like), LibraryItem.content.like(like), LibraryItem.tags.like(like))
        )
    stmt = stmt.order_by(LibraryItem.updated_at.desc())
    result = await db.execute(stmt)
    return [_item_out(i) for i in result.scalars().all()]


@router.post("/items", response_model=ItemOut)
async def create_item(data: ItemIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await _check_scope(data.novel_id, user, db)
    if data.folder_id is not None:
        folder = await db.get(LibraryFolder, data.folder_id)
        if folder is None or folder.novel_id != data.novel_id:
            raise HTTPException(400, "目录不存在")
    item = LibraryItem(**data.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return _item_out(item)


@router.put("/items/{item_id}", response_model=ItemOut)
async def update_item(
    item_id: int, data: ItemIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)
    for key, value in data.model_dump().items():
        if key == "novel_id":
            continue  # 不允许跨库移动
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return _item_out(item)


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)
    await db.delete(item)
    await db.commit()
    return {"ok": True}


# ---------- AI 辅助整理 ----------

class OrganizeOut(BaseModel):
    summary: str
    tags: list[str]
    suggested_folder: str  # 建议目录路径，如 "素材/历史原型"
    reason: str


@router.post("/items/{item_id}/organize", response_model=OrganizeOut)
async def organize_item(item_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """AI 阅读条目内容，给出摘要、标签和建议归档目录。"""
    item = await db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, "条目不存在")
    await _check_scope(item.novel_id, user, db)
    config = await get_default_ai_config(user, db)

    folders = (
        await db.execute(select(LibraryFolder).where(_scope_cond(LibraryFolder, item.novel_id)))
    ).scalars().all()
    folder_names = "、".join(f.name for f in folders) or "（暂无目录）"

    prompt = (
        "你是资料整理助手。阅读下面的资料条目，完成三件事：\n"
        "1. 写一段 50 字以内的摘要；\n"
        "2. 给出 3-6 个检索标签；\n"
        "3. 从现有目录中选一个最合适的归档目录，都不合适则建议一个新目录名。\n\n"
        f"现有目录：{folder_names}\n\n"
        f"条目标题：{item.title}\n"
        f"条目内容（节选）：\n{item.content[:3000]}\n\n"
        '严格按以下 JSON 格式输出，不要输出任何其他内容：\n'
        '{"summary": "...", "tags": ["..."], "suggested_folder": "...", "reason": "一句话理由"}'
    )
    url = config.base_url.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": 600,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {config.api_key}"})
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接 AI 接口: {exc.__class__.__name__}")
    if resp.status_code != 200:
        raise HTTPException(400, f"AI 接口返回 {resp.status_code}")

    text = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise HTTPException(502, "AI 返回格式异常，请重试")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise HTTPException(502, "AI 返回格式异常，请重试")
    return OrganizeOut(
        summary=str(data.get("summary", ""))[:500],
        tags=[str(t)[:30] for t in data.get("tags", [])][:6],
        suggested_folder=str(data.get("suggested_folder", ""))[:100],
        reason=str(data.get("reason", ""))[:200],
    )
