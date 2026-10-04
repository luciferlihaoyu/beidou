"""超长章节自动拆分（FIX-8B 从 nightly.py 挪出的共享实现）。

三条生成路径——夜跑（nightly._generate_chapter）、批量（batch.batch_run）、
UI 逐章定稿（ai_factory.finalize_chapter）——统一调用这里：

- ``split_long_chapter``：纯函数，双换行分段 + 贪心装填，可独立单测；
- ``_maybe_split_chapter``：落库动作，阈值 ``target*1.6``，第一段写回原章，
  后续段各建新章（同卷、sort_order 顺延）并配 ``status="done"`` 的任务。

为什么必须搬出来（用户现场实证）：拆分原先只挂在夜跑/批量路径，UI
「逐章生成 → finalize」不拆——38 章书里 11 章 ≥3200 字（平均 6499 字）全部
是从 finalize 漏进书稿的。放独立模块而不是让 ai_factory 反向 import nightly，
是因为 nightly 顶层已经 import ai_factory，再反向就是循环导入。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select

from .deps import count_words
from .models import AiChapterJob, AiProject, Chapter, Novel

# 场景分隔符行允许出现的符号（*** / —— / ··· / …… 这类 3+ 重复符号行）
_SEP_CHARS = set("*-—–=~·•…_.#")
_CN_NUM = "一二三四"


def _is_scene_break(line: str) -> bool:
    """场景分隔符行：整行仅含同一符号的重复（≥3 个；全角破折号/省略号 2 个即算）。"""
    s = line.strip()
    if len(s) < 2 or len(set(s)) != 1 or s[0] not in _SEP_CHARS:
        return False
    return len(s) >= 3 or s[0] in "—–…"


def split_long_chapter(text: str, target_words: int) -> list[str]:
    """超长章节自动拆分（纯函数）：双换行分段 + 贪心装填。

    - 段落累加到当前桶，桶字数 ≥ target_words 封口开新桶
    - 场景分隔符行优先作分桶边界：桶已过半（≥ target*0.5）时遇到就封口，
      分隔符行归入下一桶开头
    - 防失控：拆出份数 > 4 则不拆；拆完任一桶 < target*0.4 说明太碎，也不拆
    - 不拆时返回单元素列表（原文）
    """
    whole = text.strip()
    if not whole or target_words <= 0:
        return [text]
    paras = [p.strip() for p in re.split(r"\n{2,}", whole) if p.strip()]
    if len(paras) < 2:
        return [whole]
    buckets: list[list[str]] = []
    cur: list[str] = []
    cur_words = 0
    for para in paras:
        if cur and _is_scene_break(para) and cur_words >= target_words * 0.5:
            buckets.append(cur)
            cur, cur_words = [], 0
        cur.append(para)
        cur_words += count_words(para)
        if cur_words >= target_words:
            buckets.append(cur)
            cur, cur_words = [], 0
    if cur:
        buckets.append(cur)
    parts = ["\n\n".join(b) for b in buckets]
    if len(parts) > 4 or (
        len(parts) > 1 and any(count_words(pt) < target_words * 0.4 for pt in parts)
    ):
        return [whole]
    return parts


async def _maybe_split_chapter(p: AiProject, novel: Novel, job: AiChapterJob, chapter: Chapter, text: str, db) -> int:
    """超长自动分章落库：定稿正文超过 target_chapter_words*1.6 时拆分。

    第一段写回原章（标题不变），后续段各建新章（同卷、sort_order 顺延）并配
    status="done" 的 AiChapterJob；后续段只写正文+字数+FTS——摘要/状态文件/
    关系同步由调用方对完整章统一跑（每段都跑太贵）。返回拆出的份数（1=未拆）。
    """
    target = p.target_chapter_words or 0
    if target <= 0 or count_words(text) <= target * 1.6:
        return 1
    parts = split_long_chapter(text, target)
    n = len(parts)
    if n <= 1:
        return 1
    from .routers.ai_factory import _text_to_html  # 延迟导入避免循环

    # 第一段写回原章，原 job 对应第一段
    chapter.content = _text_to_html(parts[0])
    chapter.word_count = count_words(chapter.content)
    job.actual_words = chapter.word_count

    # 原章之后（同卷）的章节 sort_order 依次 +n-1，给新章腾位
    cond = (
        Chapter.volume_id.is_(None)
        if chapter.volume_id is None
        else Chapter.volume_id == chapter.volume_id
    )
    siblings = (
        await db.execute(
            select(Chapter).where(
                Chapter.novel_id == novel.id, cond, Chapter.sort_order > chapter.sort_order
            )
        )
    ).scalars().all()
    for sib in siblings:
        sib.sort_order += n - 1

    base = chapter.title.strip() or "未命名"
    if n == 2:
        suffixes = ["（下）"]
    elif n == 3:
        suffixes = ["（中）", "（下）"]
    else:
        suffixes = [f"·{_CN_NUM[i]}" for i in range(1, n)]  # ·二 ·三 ·四
    now = datetime.now(timezone.utc)
    new_ids: list[int] = []
    for i, part in enumerate(parts[1:], start=1):
        html = _text_to_html(part)
        new_ch = Chapter(
            novel_id=novel.id,
            volume_id=chapter.volume_id,
            title=f"{base}{suffixes[i - 1]}"[:200],
            content=html,
            sort_order=chapter.sort_order + i,
            word_count=count_words(html),
        )
        db.add(new_ch)
        await db.flush()  # 拿新章 id 建 job / 同步 FTS
        new_ids.append(new_ch.id)
        db.add(
            AiChapterJob(
                project_id=p.id,
                chapter_id=new_ch.id,
                status="done",
                actual_words=new_ch.word_count,
                attempt=1,
                finished_at=now,
            )
        )
    await db.commit()
    # FTS 同步拆出的新章（原章由调用方管线统一同步；失败不影响拆分结果）
    try:
        from .search_fts import sync_chapter

        for cid in new_ids:
            await sync_chapter(db, cid)
    except Exception:  # noqa: BLE001
        pass
    return n
