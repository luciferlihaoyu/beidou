"""章节：序号（第X章）由系统按显示顺序计算，不落库；title 只存自定义名。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import count_words, get_owned_novel
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


class ReorderIn(BaseModel):
    volume_id: int | None = None  # 在哪个分组内排序
    ordered_ids: list[int]


async def _ordered_with_numbers(novel: Novel, db: AsyncSession) -> list[tuple[Chapter, int]]:
    """按显示顺序返回 (章节, 序号) 列表。"""
    chapters = (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    volumes = (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    ordered = order_chapters(chapters, volumes)
    return [(c, i + 1) for i, c in enumerate(ordered)]


def _out(chapter: Chapter, number: int) -> dict:
    return {
        "id": chapter.id,
        "volume_id": chapter.volume_id,
        "number": number,
        "title": chapter.title,
        "display_title": chapter_display_title(chapter.title, number),
        "sort_order": chapter.sort_order,
        "word_count": chapter.word_count,
        "updated_at": chapter.updated_at,
        "content": chapter.content,
    }


@router.get("", response_model=list[ChapterOut])
async def list_chapters(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    pairs = await _ordered_with_numbers(novel, db)
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
        chapter.content = data.content
        chapter.word_count = count_words(data.content)
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
    await db.commit()
    await db.refresh(chapter)
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
