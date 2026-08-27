"""章节：序号（第X章）由系统按显示顺序计算，不落库；title 只存自定义名。

章节状态（status）：draft / writing / done；默认 draft
章节标签（tags）：自由输入，存为 JSON 数组字符串，读时 json.loads 反序列化
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import count_words, count_words_split, get_owned_novel
from ..models import Chapter, Novel, Volume
from ..utils import chapter_display_title, order_chapters

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


class ChapterOut(BaseModel):
    id: int
    volume_id: int | None
    number: int  # 全书连续序号（跨卷）
    title: str  # 自定义名，可为空
    display_title: str  # 第X章 + 自定义名
    sort_order: int
    word_count: int
    word_count_split: dict[str, int] = {}  # 双口径：{"cjk": N, "en": M, "total": K}
    status: str = "draft"  # 草稿 / 写作中 / 已完成
    tags: list[str] = []  # 自由标签，列表
    updated_at: object = None


class ChapterDetailOut(ChapterOut):
    content: str


class ChapterIn(BaseModel):
    title: str = Field(default="", max_length=200)  # 只传自定义名，不带"第X章"
    volume_id: int | None = None


class ChapterUpdateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None
    volume_id: int | None = None  # 提供时移动到其他卷（null = 未分卷）
    # status 合法值仅三档；非法值由 Pydantic 422 自动拒绝
    status: str | None = Field(default=None, pattern="^(draft|writing|done)$")
    # tags: None = 不改；显式传 [] = 清空；传 ["a","b"] = 覆盖
    tags: list[str] | None = None


class ReorderIn(BaseModel):
    volume_id: int | None = None  # 在哪个分组内排序
    ordered_ids: list[int]


async def _ordered_with_numbers(novel: Novel, db: AsyncSession) -> list[tuple[Chapter, int]]:
    """按显示顺序返回 (章节, 序号) 列表。"""
    chapters = (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    volumes = (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    ordered = order_chapters(chapters, volumes)
    return [(c, i + 1) for i, c in enumerate(ordered)]


def _filter_chapter_pairs(
    pairs: list[tuple[Chapter, int]],
    status: str | None,
    tag: list[str] | None,
) -> list[tuple[Chapter, int]]:
    """纯函数：按 status 精确匹配 + tag 包含匹配（任一命中）过滤章节对。

    拆出纯函数便于单测（直呼路由时 Query 包装不会被 FastAPI 解析，函数体内
    迭代 Query 对象会 TypeError；纯函数直传 list[str] 即可）。
    tag 匹配以引号包裹避开子串误中（如 tag="伏" 不会误中 tag="伏笔"）。
    status 与 tag 都为 None/空时直接返回原列表。
    """
    if status is None and not tag:
        return pairs
    return [
        (c, n)
        for c, n in pairs
        if (status is None or c.status == status)
        and (not tag or any(f'"{t}"' in (c.tags or "") for t in tag))
    ]


def _out(chapter: Chapter, number: int) -> dict:
    """章节转 dict。tags 是 JSON 字符串，输出前反序列化为 list[str]。

    列表端点会基于本 dict 排除 ``content`` 字段，``status`` / ``tags`` 保留。
    word_count_split 由 ``count_words_split`` 实时计算（不存 DB，避免每次
    编辑后重算所有章节快照）。
    """
    return {
        "id": chapter.id,
        "volume_id": chapter.volume_id,
        "number": number,
        "title": chapter.title,
        "display_title": chapter_display_title(chapter.title, number),
        "sort_order": chapter.sort_order,
        "word_count": chapter.word_count,
        "word_count_split": count_words_split(chapter.content or ""),
        "status": chapter.status,
        "tags": json.loads(chapter.tags or "[]"),
        "updated_at": chapter.updated_at,
        "content": chapter.content,
    }


@router.get("", response_model=list[ChapterOut])
async def list_chapters(
    status: str | None = Query(default=None, pattern="^(draft|writing|done)$"),
    tag: list[str] | None = Query(default=None),
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    # 内存过滤：_ordered_with_numbers 已全量加载章节，章数 <500 直接 Python 过滤即可，
    # 避免和 order_chapters 排序逻辑对不齐。tag 匹配以引号包裹避开子串误中。
    pairs = await _ordered_with_numbers(novel, db)
    pairs = _filter_chapter_pairs(pairs, status, tag)
    return [{k: v for k, v in _out(c, n).items() if k != "content"} for c, n in pairs]


async def _check_volume(novel: Novel, volume_id: int | None, db: AsyncSession) -> None:
    if volume_id is None:
        return
    volume = await db.get(Volume, volume_id)
    if volume is None or volume.novel_id != novel.id:
        raise HTTPException(400, "分卷不存在")


@router.post("", response_model=ChapterDetailOut)
async def create_chapter(
    data: ChapterIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    await _check_volume(novel, data.volume_id, db)
    result = await db.execute(
        select(Chapter.sort_order)
        .where(Chapter.novel_id == novel.id, Chapter.volume_id.is_(None) if data.volume_id is None else Chapter.volume_id == data.volume_id)
        .order_by(Chapter.sort_order.desc())
        .limit(1)
    )
    max_order = result.scalar_one_or_none() or 0
    chapter = Chapter(novel_id=novel.id, volume_id=data.volume_id, title=data.title.strip(), sort_order=max_order + 1)
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)
    pairs = await _ordered_with_numbers(novel, db)
    number = next(n for c, n in pairs if c.id == chapter.id)
    return _out(chapter, number)


async def _get_chapter(novel: Novel, chapter_id: int, db: AsyncSession) -> Chapter:
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None or chapter.novel_id != novel.id:
        raise HTTPException(404, "章节不存在")
    return chapter


@router.get("/{chapter_id}", response_model=ChapterDetailOut)
async def get_chapter(
    chapter_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    chapter = await _get_chapter(novel, chapter_id, db)
    pairs = await _ordered_with_numbers(novel, db)
    number = next(n for c, n in pairs if c.id == chapter.id)
    return _out(chapter, number)


@router.put("/{chapter_id}", response_model=ChapterDetailOut)
async def update_chapter(
    chapter_id: int,
    data: ChapterUpdateIn,
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _get_chapter(novel, chapter_id, db)
    if data.title is not None:
        chapter.title = data.title.strip()
    if data.content is not None:
        new_count = count_words(data.content)
        delta = new_count - chapter.word_count
        chapter.content = data.content
        chapter.word_count = new_count
        if delta > 0:
            from .stats import record_writing

            await record_writing(db, novel.id, delta)
    if "volume_id" in data.model_fields_set and data.volume_id != chapter.volume_id:
        await _check_volume(novel, data.volume_id, db)
        # 移到目标卷末尾
        result = await db.execute(
            select(Chapter.sort_order)
            .where(Chapter.novel_id == novel.id, Chapter.volume_id.is_(None) if data.volume_id is None else Chapter.volume_id == data.volume_id)
            .order_by(Chapter.sort_order.desc())
            .limit(1)
        )
        chapter.sort_order = (result.scalar_one_or_none() or 0) + 1
        chapter.volume_id = data.volume_id
    # status / tags：None 表示不改；显式传值则覆盖（Pydantic pattern 已校验 status）
    if data.status is not None:
        chapter.status = data.status
    if data.tags is not None:
        # ensure_ascii=False 保留中文标签原文；list → JSON 字符串存进 DB
        chapter.tags = json.dumps(data.tags, ensure_ascii=False)
    await db.commit()
    await db.refresh(chapter)
    # 章节正文更新后挂接 auto 快照：节流 + hash 去重，副作用忽略（失败也不应影响编辑）
    if data.content is not None:
        from ..snapshot_service import create_snapshot

        try:
            await create_snapshot(db, chapter, "auto")
        except Exception:  # noqa: BLE001  快照失败不影响章节保存
            pass
    pairs = await _ordered_with_numbers(novel, db)
    number = next(n for c, n in pairs if c.id == chapter.id)
    return _out(chapter, number)


@router.delete("/{chapter_id}")
async def delete_chapter(
    chapter_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    chapter = await _get_chapter(novel, chapter_id, db)
    await db.delete(chapter)
    await db.commit()
    return {"ok": True}


@router.post("/reorder")
async def reorder_chapters(
    data: ReorderIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    """在指定分组（某卷或未分卷）内重排章节。"""
    cond = Chapter.volume_id.is_(None) if data.volume_id is None else Chapter.volume_id == data.volume_id
    result = await db.execute(select(Chapter).where(Chapter.novel_id == novel.id, cond))
    chapters = {c.id: c for c in result.scalars().all()}
    if set(data.ordered_ids) != set(chapters.keys()):
        raise HTTPException(400, "章节列表不匹配")
    for index, chapter_id in enumerate(data.ordered_ids):
        chapters[chapter_id].sort_order = index
    await db.commit()
    return {"ok": True}
