"""章节快照/存稿点服务：节流 + hash 去重 + 防呆回滚 + diff。

设计要点：
- 同 hash 不重复：同一章节同内容已有快照时直接返回已有，避免无意义膨胀
- auto 触发节流：同章节距最近 auto 快照 < `AUTO_SNAPSHOT_INTERVAL` 秒则跳过；
  思源默认 10 分钟，作者连续敲字也不至于把表撑爆
- pre_rollback 必存：回滚流程不经过节流/去重，保证当前内容一定有迹可循
"""

import difflib
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Chapter, ChapterSnapshot, utcnow
from .utils import strip_html

# auto 触发的默认节流间隔（秒）。调小会更频繁、调大会更稀疏
AUTO_SNAPSHOT_INTERVAL = 600


def _as_utc(dt: datetime) -> datetime:
    """SQLite 不存时区，从 DB 读出的 datetime 是 naive。比较前补 UTC 标记避免与
    aware datetime 直接比较时抛 TypeError（生产 bug：在已有 auto 快照的章节上
    触发第二次 auto 会把章节保存整个打挂）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _content_hash(content: str) -> str:
    """基于章节 HTML 计算 sha256 摘要（hex）。"""
    return hashlib.sha256(content.encode()).hexdigest()


async def create_snapshot(
    db: AsyncSession,
    chapter: Chapter,
    trigger: str,
    label: str = "",
    *,
    interval_seconds: int = AUTO_SNAPSHOT_INTERVAL,
) -> ChapterSnapshot | None:
    """根据 chapter 当前内容创建快照。

    去重与节流策略：
    1. 任何 trigger 都会先查同 chapter+content_hash 是否已存在 → 命中则直接返回已有
    2. 仅 trigger=="auto" 时再做时间窗节流：距最近 auto 快照 < interval_seconds → 返回 None
    3. 上述都通过才 INSERT 并 commit，返回新对象

    返回 None 表示"此次无操作"（节流命中），由调用方自行处理；
    返回已有对象表示"已存在等价快照，跳过新建"。
    """
    content = chapter.content or ""
    content_hash = _content_hash(content)
    content_text = strip_html(content)
    word_count = chapter.word_count

    # 1) 同 hash 去重（同章节内同内容已存过）
    existing = await db.execute(
        select(ChapterSnapshot).where(
            ChapterSnapshot.chapter_id == chapter.id,
            ChapterSnapshot.content_hash == content_hash,
        )
    )
    hit = existing.scalars().first()
    if hit is not None:
        return hit

    # 2) auto 触发节流：同章节最近一次 auto 快照
    if trigger == "auto" and interval_seconds > 0:
        recent = await db.execute(
            select(ChapterSnapshot)
            .where(ChapterSnapshot.chapter_id == chapter.id, ChapterSnapshot.trigger == "auto")
            .order_by(ChapterSnapshot.created_at.desc())
            .limit(1)
        )
        last_auto = recent.scalars().first()
        if last_auto is not None:
            threshold = _as_utc(last_auto.created_at) + timedelta(seconds=interval_seconds)
            if utcnow() < threshold:
                return None

    snap = ChapterSnapshot(
        chapter_id=chapter.id,
        content=content,
        content_text=content_text,
        word_count=word_count,
        content_hash=content_hash,
        label=label or "",
        trigger=trigger,
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return snap


async def restore_snapshot(
    db: AsyncSession,
    chapter: Chapter,
    snapshot_id: int,
) -> tuple[ChapterSnapshot, ChapterSnapshot]:
    """回滚流程：

    1. 先为 chapter 当前内容创建 pre_rollback 快照（不走节流/去重，必存）
    2. 把目标快照的 content/word_count 写回 chapter，并更新 updated_at
    3. 一次 commit
    返回 (pre_rollback_snapshot, restored_snapshot) — 前端可告知用户已自动存档。
    """
    target = await db.get(ChapterSnapshot, snapshot_id)
    if target is None or target.chapter_id != chapter.id:
        from fastapi import HTTPException

        raise HTTPException(404, "快照不存在")

    # 1) pre_rollback：直接落库，不走去重/节流
    pre = ChapterSnapshot(
        chapter_id=chapter.id,
        content=chapter.content or "",
        content_text=strip_html(chapter.content or ""),
        word_count=chapter.word_count,
        content_hash=_content_hash(chapter.content or ""),
        label="",
        trigger="pre_rollback",
    )
    db.add(pre)
    await db.flush()  # 拿 pre.id，但暂不 commit

    # 2) 把目标快照内容写回章节
    chapter.content = target.content
    chapter.word_count = target.word_count
    chapter.updated_at = utcnow()  # onupdate 已会做，但显式更稳

    await db.commit()
    await db.refresh(pre)
    await db.refresh(target)
    return pre, target


def diff_html(text_a: str, text_b: str) -> str:
    """基于 difflib.HtmlDiff 渲染两段文本的差异（按行）。

    返回仅包含 ``<table class="diff">`` 的 body 片段，前端用
    ``dangerouslySetInnerHTML`` 渲染。内容是用户自己的章节文本，零脚本注入风险。
    """
    from_lines = (text_a or "").splitlines()
    to_lines = (text_b or "").splitlines()
    differ = difflib.HtmlDiff(wrapcolumn=80)
    return differ.make_table(from_lines, to_lines, "较早", "较晚", context=True)
