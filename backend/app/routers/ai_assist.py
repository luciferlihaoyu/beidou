"""人工写作 AI 助手：编辑器内的续写 / 润色 / 头脑风暴（SSE 流式）。

与 AI 工厂（ai_factory）平行：AI 工厂管「自动化流水线」，本模块管「人工写作
编辑器里的即兴辅助」——光标处续写、选中文字润色、卡文时头脑风暴。

设计要点：
- 单一端点 POST /api/novels/{novel_id}/ai-assist，action 三选一
- 复用 ai._stream_openai 的 SSE 转发（data: {content}/{done}/{error} 与前端 streamPost 兼容）
- 配置用 ai_factory._pick_config(user, db, "") 取默认配置
- 三种 action 的 system prompt 均注入 anti_llm.ANTI_LLM_RULES（去 AI 味铁律）
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..anti_llm import ANTI_LLM_RULES
from ..db import get_db
from ..deps import get_current_user, get_owned_novel
from ..models import Chapter, Novel, User
from ..utils import strip_html
from .ai import _stream_openai
from .ai_factory import _pick_config

router = APIRouter(prefix="/api/novels", tags=["ai-assist"])

# 润色选中文字上限（超出截断保护，不报错）
POLISH_MAX = 3000
# 续写上下文：取章节正文末尾字数
CONTINUE_TAIL = 2000
# 头脑风暴最多带上的已有章节条数
BRAINSTORM_CHAPTERS = 10


class AiAssistIn(BaseModel):
    action: Literal["continue", "polish", "brainstorm"]
    selected_text: str = Field(default="", max_length=20000)
    instruction: str = Field(default="", max_length=1000)
    chapter_id: int | None = None


_SYSTEM_BASE = (
    "你是一位资深中文网文创作助手，熟悉起点/番茄/晋江的节奏、爽点与读者期待。"
    "输出使用简体中文，直接给出可用内容，不要解释、不要客套。\n\n" + ANTI_LLM_RULES
)

_SYSTEM_CONTINUE = (
    _SYSTEM_BASE
    + "\n\n当前任务：无缝续写。你是本书作者的代笔，续写必须承接上文的场景、视角、"
    "情绪与行文节奏，读起来像同一个人一气呵成写下的。只输出正文，不要章节标题、不要任何解释。"
)

_SYSTEM_POLISH = (
    _SYSTEM_BASE
    + "\n\n当前任务：文字润色。你是严苛而克制的一线编辑：修正语病与标点、提升画面感与节奏，"
    "但严格保持原意、情节走向、人物关系与视角不变；篇幅保持在原文 ±20% 以内，"
    "不增删情节，不擅自升华。只输出润色后的文字本身，不要解释修改点。"
)

_SYSTEM_BRAINSTORM = (
    _SYSTEM_BASE
    + "\n\n当前任务：头脑风暴。你是与作者并肩的资深剧情策划，给的建议必须具体可写、"
    "贴合本书已有设定与风格，杜绝放之四海皆准的空话。"
)


def _novel_brief(novel: Novel) -> str:
    parts = [f"作品：《{novel.title}》"]
    if novel.genre:
        parts.append(f"类型：{novel.genre}")
    if novel.description:
        parts.append(f"简介：{novel.description}")
    return "\n".join(parts)


@router.post("/{novel_id}/ai-assist")
async def ai_assist(
    novel_id: int,
    data: AiAssistIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """编辑器 AI 助手统一入口（SSE 流式）。

    - continue：基于当前章节末尾 + 书名/简介续写 300-600 字
    - polish：润色 selected_text（超长截断到 3000 字）
    - brainstorm：基于书名/简介/已有章节摘要，按卡文点给 3 个剧情走向
    """
    novel = await get_owned_novel(novel_id, user, db)
    config = await _pick_config(user, db, "")
    instruction = data.instruction.strip()

    if data.action == "continue":
        if data.chapter_id is None:
            raise HTTPException(400, "续写需要先打开一个章节")
        chapter = await db.get(Chapter, data.chapter_id)
        if chapter is None or chapter.novel_id != novel.id:
            raise HTTPException(404, "章节不存在")
        tail = strip_html(chapter.content).strip()[-CONTINUE_TAIL:]
        if len(tail) < 10:
            raise HTTPException(400, "章节内容太少，先自己写一小段再来续写")
        prompt = (
            f"{_novel_brief(novel)}\n\n"
            f"当前章节《{chapter.title or '未命名'}》结尾（最近 {len(tail)} 字）：\n{tail}\n\n"
            "请无缝续写 300-600 字正文，承接上文的场景、情绪与节奏。"
        )
        if instruction:
            prompt += f"\n作者补充指示：{instruction}"
        messages = [
            {"role": "system", "content": _SYSTEM_CONTINUE},
            {"role": "user", "content": prompt},
        ]

    elif data.action == "polish":
        text = data.selected_text.strip()
        if not text:
            raise HTTPException(400, "请先在编辑器中选中要润色的文字")
        if len(text) > POLISH_MAX:
            text = text[:POLISH_MAX]  # 超长截断保护
        prompt = (
            f"{_novel_brief(novel)}\n\n"
            f"待润色的文字（共 {len(text)} 字）：\n{text}\n\n"
            "请按系统提示中的润色要求改写这段文字。"
        )
        if instruction:
            prompt += f"\n作者补充指示：{instruction}"
        messages = [
            {"role": "system", "content": _SYSTEM_POLISH},
            {"role": "user", "content": prompt},
        ]

    else:  # brainstorm
        chapters = (
            await db.execute(
                select(Chapter).where(Chapter.novel_id == novel.id).order_by(Chapter.id).limit(BRAINSTORM_CHAPTERS)
            )
        ).scalars().all()
        digest = "\n".join(
            f"{i + 1}. 《{c.title or '未命名'}》：{strip_html(c.content).strip()[:120]}"
            for i, c in enumerate(chapters)
            if strip_html(c.content).strip()
        )
        prompt = _novel_brief(novel)
        if digest:
            prompt += f"\n\n已有章节概要：\n{digest}"
        prompt += (
            f"\n\n作者现在卡在这里：{instruction or '（未填写，请基于现有内容判断最顺的走向）'}\n\n"
            "请给出 3 个剧情走向建议，每个建议包含：①一句话概述 ②核心冲突点 ③留给读者的钩子。"
            "每条 100 字以内，分条列出。"
        )
        messages = [
            {"role": "system", "content": _SYSTEM_BRAINSTORM},
            {"role": "user", "content": prompt},
        ]

    return await _stream_openai(config, messages)
