"""全书查找与替换：只处理正文文本节点，不动 HTML 标签与属性。"""

import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import count_words, get_owned_novel
from ..models import Chapter, Novel, Volume
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
    return {"ok": True, "replaced": total, "chapters_affected": affected}
