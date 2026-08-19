"""分卷：卷的新建/重命名/排序/删除。删除卷时章节移到"未分卷"，不丢内容。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_owned_novel
from ..models import Chapter, Novel, Volume

router = APIRouter(prefix="/api/novels/{novel_id}/volumes", tags=["volumes"])


class VolumeOut(BaseModel):
    id: int
    title: str
    sort_order: int
    chapter_count: int = 0
    word_count: int = 0


class VolumeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ReorderIn(BaseModel):
    ordered_ids: list[int]


async def _volume_stats(novel: Novel, db: AsyncSession) -> dict[int | None, tuple[int, int]]:
    """volume_id → (章节数, 总字数)"""
    result = await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))
    stats: dict[int | None, list[int]] = {}
    for c in result.scalars().all():
        entry = stats.setdefault(c.volume_id, [0, 0])
        entry[0] += 1
        entry[1] += c.word_count
    return {k: (v[0], v[1]) for k, v in stats.items()}


@router.get("", response_model=list[VolumeOut])
async def list_volumes(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    volumes = (
        await db.execute(select(Volume).where(Volume.novel_id == novel.id).order_by(Volume.sort_order, Volume.id))
    ).scalars().all()
    stats = await _volume_stats(novel, db)
    return [
        VolumeOut(
            id=v.id,
            title=v.title,
            sort_order=v.sort_order,
            chapter_count=stats.get(v.id, (0, 0))[0],
            word_count=stats.get(v.id, (0, 0))[1],
        )
        for v in volumes
    ]


@router.post("", response_model=VolumeOut)
async def create_volume(data: VolumeIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Volume.sort_order).where(Volume.novel_id == novel.id).order_by(Volume.sort_order.desc()).limit(1)
    )
    max_order = result.scalar_one_or_none() or 0
    volume = Volume(novel_id=novel.id, title=data.title.strip(), sort_order=max_order + 1)
    db.add(volume)
    await db.commit()
    await db.refresh(volume)
    return VolumeOut(id=volume.id, title=volume.title, sort_order=volume.sort_order)


async def _get_volume(novel: Novel, volume_id: int, db: AsyncSession) -> Volume:
    volume = await db.get(Volume, volume_id)
    if volume is None or volume.novel_id != novel.id:
        raise HTTPException(404, "分卷不存在")
    return volume


@router.put("/{volume_id}", response_model=VolumeOut)
async def rename_volume(
    volume_id: int, data: VolumeIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    volume = await _get_volume(novel, volume_id, db)
    volume.title = data.title.strip()
    await db.commit()
    stats = await _volume_stats(novel, db)
    count, words = stats.get(volume.id, (0, 0))
    return VolumeOut(id=volume.id, title=volume.title, sort_order=volume.sort_order,
                     chapter_count=count, word_count=words)


@router.delete("/{volume_id}")
async def delete_volume(volume_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    volume = await _get_volume(novel, volume_id, db)
    # 卷内章节移到未分卷（不依赖 SQLite 外键开关，显式处理）
    chapters = (await db.execute(select(Chapter).where(Chapter.volume_id == volume.id))).scalars().all()
    for c in chapters:
        c.volume_id = None
    await db.delete(volume)
    await db.commit()
    return {"ok": True}


@router.post("/reorder")
async def reorder_volumes(data: ReorderIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Volume).where(Volume.novel_id == novel.id))
    volumes = {v.id: v for v in result.scalars().all()}
    if set(data.ordered_ids) != set(volumes.keys()):
        raise HTTPException(400, "分卷列表不匹配")
    for index, volume_id in enumerate(data.ordered_ids):
        volumes[volume_id].sort_order = index
    await db.commit()
    return {"ok": True}
