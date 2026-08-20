"""技能卡：内置网文创作技能卡（SKILL.md），作为 AI 面板的快捷指令。

卡片来源：novel-skill-cards 仓库，每张卡为一份 SKILL.md（YAML frontmatter + 正文）。
点击卡片时，将卡片正文注入 prompt，叠加当前作品上下文后流式请求 AI。
"""

import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_ai_config, get_current_user, get_owned_novel
from ..models import User
from .ai import _novel_context, _stream_openai, SYSTEM_PROMPT

router = APIRouter(prefix="/api/skills", tags=["skills"])

CARD_DIR = Path(__file__).parent.parent / "skillcards"

# slug → (显示名, 分类, 一句话用途)
CARD_META: dict[str, tuple[str, str, str]] = {
    "outline-architect": ("大纲架构师", "create", "主线/升级线/伏笔三线合一，卷结构+章级细纲"),
    "worldbuilding-bible": ("设定集构建", "create", "力量体系、势力、地理、经济，产出世界观圣经"),
    "opening-crafter": ("开篇打磨", "create", "黄金三章设计与诊断，立人/立困/立钩"),
    "chapter-hook-crafter": ("章节卡点", "create", "六大章末钩子，断点位置+结尾改写"),
    "name-forge": ("取名炉", "create", "人名/地名/功法/势力批量生成，附寓意与筛查"),
    "book-packager": ("文案包装", "create", "书名/简介/标签/章节名，按平台范式产出"),
    "novel-consistency-checker": ("穿帮检查", "check", "数值/设定口径/细节连续三层核对"),
    "webnovel-pace-analyzer": ("节奏分析", "check", "逐章扫描钩子与爽点，定位爽点荒与水文区"),
    "deai-rewrite": ("去AI味改写", "check", "AI高频词预警 + 六类AI腔改写"),
    "style-fingerprint": ("文风指纹", "check", "句长/对话占比/高频词测量，用于续写对齐"),
    "novel-deconstruction": ("拆书分析", "check", "黄金三章/情节/人物/世界观/爽点五维拆解"),
}

CATEGORY_LABEL = {"create": "创作生产", "check": "诊断改稿"}


def _parse_card(slug: str) -> dict | None:
    path = CARD_DIR / f"{slug}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    body = m.group(2).strip() if m else text.strip()
    fm = m.group(1) if m else ""
    # folded YAML description（`>` 后多行缩进文本），取首行作为简介
    desc = ""
    dm = re.search(r"^description:\s*>?\s*\n((?:[ \t]+.+\n?)+)", fm, re.M)
    if dm:
        desc = dm.group(1).strip().splitlines()[0].strip()
    else:
        dm2 = re.search(r"^description:\s*(.+)$", fm, re.M)
        if dm2:
            desc = dm2.group(1).strip()
    zh, cat, brief = CARD_META.get(slug, (slug, "create", ""))
    return {
        "slug": slug,
        "name": zh,
        "category": cat,
        "category_label": CATEGORY_LABEL[cat],
        "brief": brief,
        "description": desc[:120],
        "body": body,
    }


@lru_cache
def _all_cards() -> list[dict]:
    cards = []
    for slug in CARD_META:
        card = _parse_card(slug)
        if card:
            cards.append(card)
    return cards


def get_card(slug: str) -> dict | None:
    """按 slug 取技能卡（供 AI 对话挂接使用）。"""
    return next((c for c in _all_cards() if c["slug"] == slug), None)


@router.get("")
async def list_skills(user: User = Depends(get_current_user)):
    return [
        {k: c[k] for k in ("slug", "name", "category", "category_label", "brief", "description")}
        for c in _all_cards()
    ]


class SkillRunIn(BaseModel):
    novel_id: int
    chapter_id: int | None = None
    instruction: str = Field(default="", max_length=2000)
    config_id: int | None = None


@router.post("/{slug}/run")
async def run_skill(
    slug: str, data: SkillRunIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    card = next((c for c in _all_cards() if c["slug"] == slug), None)
    if card is None:
        from fastapi import HTTPException

        raise HTTPException(404, "技能卡不存在")

    novel = await get_owned_novel(data.novel_id, user, db)
    config = await get_ai_config(user, db, data.config_id)
    context = await _novel_context(novel, db, data.chapter_id)

    task = data.instruction.strip() or f"请运用「{card['name']}」技能，基于以上作品信息开始工作，并主动给出产出。"
    prompt = (
        f"以下是一项专业写作技能的完整工作手册，请严格遵循其中的流程与标准执行：\n\n"
        f"---\n{card['body']}\n---\n\n"
        f"当前作品信息：\n{context}\n\n"
        f"任务：{task}"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    return await _stream_openai(config, messages)
