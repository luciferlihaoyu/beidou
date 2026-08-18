from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import count_words, get_owned_novel
from ..models import Chapter, Novel

router = APIRouter(prefix="/api/novels/{novel_id}/chapters", tags=["chapters"])


class ChapterOut(BaseModel):
    id: int
    title: str
    sort_order: int
    word_count: int
    updated_at: object = None

    model_config = {"from_attributes": True}


class ChapterDetailOut(ChapterOut):
    content: str


class ChapterIn(BaseModel):
    title: str = Field(default="未命名章节", max_length=200)


class ChapterUpdateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None


class ReorderIn(BaseModel):
    ordered_ids: list[int]


@router.get("", response_model=list[ChapterOut])
async def list_chapters(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Chapter).where(Chapter.novel_id == novel.id).order_by(Chapter.sort_order, Chapter.id)
    )
    return result.scalars().all()


@router.post("", response_model=ChapterDetailOut)
async def create_chapter(
    data: ChapterIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Chapter.sort_order).where(Chapter.novel_id == novel.id).order_by(Chapter.sort_order.desc()).limit(1)
    )
    max_order = result.scalar_one_or_none() or 0
    chapter = Chapter(novel_id=novel.id, title=data.title, sort_order=max_order + 1)
    db.add(chapter)
    await db.commit()
    await db.refresh(chapter)
    return chapter


async def _get_chapter(novel: Novel, chapter_id: int, db: AsyncSession) -> Chapter:
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None or chapter.novel_id != novel.id:
        raise HTTPException(404, "章节不存在")
    return chapter


@router.get("/{chapter_id}", response_model=ChapterDetailOut)
async def get_chapter(
    chapter_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    return await _get_chapter(novel, chapter_id, db)


@router.put("/{chapter_id}", response_model=ChapterDetailOut)
async def update_chapter(
    chapter_id: int,
    data: ChapterUpdateIn,
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    chapter = await _get_chapter(novel, chapter_id, db)
    if data.title is not None:
        chapter.title = data.title
    if data.content is not None:
        chapter.content = data.content
        chapter.word_count = count_words(data.content)
    await db.commit()
    await db.refresh(chapter)
    return chapter


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
    result = await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))
    chapters = {c.id: c for c in result.scalars().all()}
    if set(data.ordered_ids) != set(chapters.keys()):
        raise HTTPException(400, "章节列表不匹配")
    for index, chapter_id in enumerate(data.ordered_ids):
        chapters[chapter_id].sort_order = index
    await db.commit()
    return {"ok": True}
