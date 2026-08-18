from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_current_user, get_owned_novel
from ..models import Chapter, Novel, User

router = APIRouter(prefix="/api/novels", tags=["novels"])

COVER_COLORS = ["#004EFF", "#0EA5E9", "#6366F1", "#0F766E", "#B45309", "#BE123C", "#4D7C0F", "#24272A"]


class NovelIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = Field(default="", max_length=100)
    description: str = ""
    genre: str = Field(default="", max_length=50)
    status: str = Field(default="连载中", max_length=20)
    cover_color: str = Field(default="#004EFF", max_length=20)


class NovelOut(BaseModel):
    id: int
    title: str
    author: str
    description: str
    genre: str
    status: str
    cover_color: str
    chapter_count: int = 0
    total_words: int = 0
    updated_at: object = None

    model_config = {"from_attributes": True}


async def _with_stats(db: AsyncSession, novels: list[Novel]) -> list[NovelOut]:
    if not novels:
        return []
    ids = [n.id for n in novels]
    result = await db.execute(
        select(Chapter.novel_id, func.count(Chapter.id), func.coalesce(func.sum(Chapter.word_count), 0))
        .where(Chapter.novel_id.in_(ids))
        .group_by(Chapter.novel_id)
    )
    stats = {row[0]: (row[1], row[2]) for row in result.all()}
    out = []
    for n in novels:
        item = NovelOut.model_validate(n)
        item.chapter_count, item.total_words = stats.get(n.id, (0, 0))
        item.updated_at = n.updated_at
        out.append(item)
    return out


@router.get("", response_model=list[NovelOut])
async def list_novels(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    query = select(Novel).order_by(Novel.updated_at.desc())
    if user.role != "admin":
        query = query.where(Novel.user_id == user.id)
    result = await db.execute(query)
    return await _with_stats(db, list(result.scalars().all()))


@router.post("", response_model=NovelOut)
async def create_novel(data: NovelIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    novel = Novel(user_id=user.id, **data.model_dump())
    db.add(novel)
    await db.commit()
    await db.refresh(novel)
    return NovelOut.model_validate(novel)


@router.get("/{novel_id}", response_model=NovelOut)
async def get_novel(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    return (await _with_stats(db, [novel]))[0]


@router.put("/{novel_id}", response_model=NovelOut)
async def update_novel(
    data: NovelIn,
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    for key, value in data.model_dump().items():
        setattr(novel, key, value)
    await db.commit()
    await db.refresh(novel)
    return (await _with_stats(db, [novel]))[0]


@router.delete("/{novel_id}")
async def delete_novel(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    await db.delete(novel)
    await db.commit()
    return {"ok": True}
