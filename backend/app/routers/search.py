"""全书查找与替换：只处理正文文本节点，不动 HTML 标签与属性。

新增 /api/novels/{id}/search/fts（P3-1）：
- 用 FTS5 虚拟表（chapter_fts）快速全文搜索
- 返回带 <mark> 高亮的 snippet
- 写入：chapters save 时调 search_fts.sync_chapter 同步
- 首次启动：db._migrate 全量灌入存量章节
"""

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import count_words, get_owned_novel
from ..models import Chapter, Novel, Volume
from ..search_fts import search_fts
from ..utils import chapter_display_title, order_chapters

router = APIRouter(prefix="/api/novels/{novel_id}/search", tags=["search"])

_TAG_SPLIT = re.compile(r"(<[^>]+>)")


def _text_parts(html: str):
    """拆分 HTML，产出 (是否文本节点, 片段)。"""
    for part in _TAG_SPLIT.split(html):
        yield (not part.startswith("<"), part)


def _count_in_text(html: str, q: str) -> int:
    return sum(part.count(q) for is_text, part in _text_parts(html) if is_text)


def _replace_in_text(html: str, q: str, repl: str) -> tuple[str, int]:
    out: list[str] = []
    total = 0
    for is_text, part in _text_parts(html):
        if is_text and q in part:
            total += part.count(q)
            part = part.replace(q, repl)
        out.append(part)
    return "".join(out), total


async def _ordered_chapters(novel: Novel, db: AsyncSession) -> list[Chapter]:
    chapters = (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    volumes = (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    return order_chapters(chapters, volumes)


@router.get("")
async def search_novel(
    q: str = Query(min_length=1, max_length=100),
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """全书查找：返回每章命中次数，按显示顺序。"""
    results = []
    for i, chapter in enumerate(await _ordered_chapters(novel, db)):
        count = _count_in_text(chapter.content, q)
        if count:
            results.append(
                {
                    "chapter_id": chapter.id,
                    "display_title": chapter_display_title(chapter.title, i + 1),
                    "count": count,
                }
            )
    return {"query": q, "total": sum(r["count"] for r in results), "results": results}


class ReplaceIn(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    replacement: str = Field(default="", max_length=200)


@router.post("/replace")
async def replace_all(
    data: ReplaceIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    """全书替换：只替换文本节点内的匹配，替换后重算章节字数。"""
    total = 0
    affected = 0
    for chapter in await _ordered_chapters(novel, db):
        new_content, n = _replace_in_text(chapter.content, data.query, data.replacement)
        if n:
            chapter.content = new_content
            chapter.word_count = count_words(new_content)
            total += n
            affected += 1
    await db.commit()
    # 替换后批量同步 FTS（重置全小说索引最快）
    if affected > 0:
        try:
            from ..search_fts import rebuild_fts_for_novel

            await rebuild_fts_for_novel(db, novel.id)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "replaced": total, "chapters_affected": affected}


@router.get("/fts")
async def search_fts_endpoint(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=30, ge=1, le=100),
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    """FTS5 全文搜索（P3-1）：返回带高亮 snippet 的命中章节。

    排序：FTS5 bm25 排名（更相关的章节更靠前）。
    短语查询：纯中文自动加双引号变为 phrase（避免单词命中）。
    """
    results = await search_fts(db, novel.id, q, limit=limit)
    # 按 display 顺序补序号
    chapters = await _ordered_chapters(novel, db)
    index_by_id = {ch.id: i + 1 for i, ch in enumerate(chapters)}
    out = []
    for r in results:
        num = index_by_id.get(r["chapter_id"])
        out.append(
            {
                "chapter_id": r["chapter_id"],
                "display_title": chapter_display_title(r["title"] or "", num or 0),
                "count": r["count"],
                "snippet": r["snippet"],
            }
        )
    return {"query": q, "total": sum(r["count"] for r in out), "results": out}
