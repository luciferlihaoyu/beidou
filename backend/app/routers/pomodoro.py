"""番茄钟完成事件：用户完成一个番茄时上报一行；今日/累计统计从 DB 查。

设计：
- 番茄钟的"计时"逻辑仍在前端（前端写 usePomodoro 状态机，避免 WebSocket 复杂度）
- 完成时（phase=write 25min 到点）前端 POST /api/pomodoro/complete 写一行
- 多端/移动端共享：今日番茄数、累计都从 DB 聚合
- 跨日 reset：按北京时间（UTC+8）当日计算，与 stats.record_writing 口径一致
  （A5 修复：原 UTC 当日导致北京时间 0:00-8:00 的番茄计入前一天）
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_current_user
from ..models import Novel, PomoLog, User

router = APIRouter(prefix="/api/pomodoro", tags=["pomodoro"])

_BEIJING = timezone(timedelta(hours=8))


class CompleteIn(BaseModel):
    """上报一个完成的番茄。"""

    novel_id: int | None = None
    phase: str = Field(default="write", pattern=r"^(write|break)$")
    duration_min: int = Field(default=25, ge=1, le=120)


class TodayOut(BaseModel):
    today: int
    total: int
    date: str  # YYYY-MM-DD（北京时间当日）


def _today_beijing_range() -> tuple[datetime, datetime]:
    """北京时间当日 [00:00, 24:00)，转回 UTC 用于 DB 比较。"""
    now_bj = datetime.now(_BEIJING)
    start_bj = now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
    end_bj = start_bj + timedelta(days=1)
    return start_bj.astimezone(timezone.utc), end_bj.astimezone(timezone.utc)


@router.post("/complete")
async def complete_pomo(
    data: CompleteIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """前端 usePomodoro 到点时调用：写一行 PomoLog。
    - 校验 novel 属于当前用户
    - 返回今日累计（写完后客户端可同步刷新）"""
    if data.novel_id is not None:
        novel = await db.get(Novel, data.novel_id)
        if novel is None or novel.owner_id != user.id:
            raise HTTPException(404, "小说不存在")
    log = PomoLog(
        user_id=user.id,
        novel_id=data.novel_id,
        phase=data.phase,
        duration_min=data.duration_min,
    )
    db.add(log)
    await db.commit()
    today_start, _ = _today_beijing_range()
    today_count = (
        await db.execute(
            select(func.count(PomoLog.id)).where(
                PomoLog.user_id == user.id,
                PomoLog.phase == "write",
                PomoLog.completed_at >= today_start,
            )
        )
    ).scalar_one()
    return {"ok": True, "today": today_count}


@router.get("/today", response_model=TodayOut)
async def today_pomo(
    novel_id: int | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """今日番茄数（按北京时间当日，phase=write）+ 累计数（phase=write）。

    累计数 = 用户所有 phase=write 记录数（不限日期）。
    """
    today_start, _ = _today_beijing_range()
    if novel_id is not None:
        novel = await db.get(Novel, novel_id)
        if novel is None or novel.owner_id != user.id:
            raise HTTPException(404, "小说不存在")
    today_q = select(func.count(PomoLog.id)).where(
        PomoLog.user_id == user.id,
        PomoLog.phase == "write",
        PomoLog.completed_at >= today_start,
    )
    if novel_id is not None:
        today_q = today_q.where(PomoLog.novel_id == novel_id)
    total_q = select(func.count(PomoLog.id)).where(
        PomoLog.user_id == user.id,
        PomoLog.phase == "write",
    )
    if novel_id is not None:
        total_q = total_q.where(PomoLog.novel_id == novel_id)
    today_count = (await db.execute(today_q)).scalar_one()
    total_count = (await db.execute(total_q)).scalar_one()
    return TodayOut(
        today=today_count,
        total=total_count,
        date=datetime.now(_BEIJING).date().isoformat(),
    )
