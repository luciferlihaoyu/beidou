"""写作统计：每日字数累计 + 码字日历数据（按北京时间记日）。"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_owned_novel
from ..models import Novel, WritingStat

router = APIRouter(prefix="/api/novels/{novel_id}/stats", tags=["stats"])

_BEIJING = timezone(timedelta(hours=8))


def today_str() -> str:
    return datetime.now(_BEIJING).strftime("%Y-%m-%d")


async def record_writing(db: AsyncSession, novel_id: int, delta: int) -> None:
    """把字数正增量记入当天（调用方负责 commit）。"""
    if delta <= 0:
        return
    day = today_str()
    stat = (
        await db.execute(
            select(WritingStat).where(WritingStat.novel_id == novel_id, WritingStat.date == day)
        )
    ).scalar_one_or_none()
    if stat is None:
        db.add(WritingStat(novel_id=novel_id, date=day, words=delta))
    else:
        stat.words += delta


class DailyStat(BaseModel):
    date: str
    words: int


@router.get("/daily", response_model=list[DailyStat])
async def daily_stats(
    days: int = Query(default=120, ge=1, le=365),
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    since = (datetime.now(_BEIJING) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    result = await db.execute(
        select(WritingStat)
        .where(WritingStat.novel_id == novel.id, WritingStat.date >= since)
        .order_by(WritingStat.date)
    )
    return [{"date": s.date, "words": s.words} for s in result.scalars().all()]
