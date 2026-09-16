"""AI 工厂 M14：拆书学习（参考书 → 范式笔记 → 注入新书立项/设定）。

与 M4「导入续写」的区别：
- M4：导入一本书，逆向出它的真相文件，然后**接着写这本书**。
- M14：拆解一本（或几本）参考书，提炼**可复用的写法范式**（世界观结构、
  力量体系、人物配置、节奏爽点、钩子模式、语言调性、避坑清单），存成
  可编辑的「范式笔记」挂在项目上；生成立项草案与设定时注入——借结构与
  节奏，人物/地名/世界观全部重写，作者再逐项改。

成本护栏：单次拆解文本 ≤ 30 万字（超出截断），提取用单次非流式调用。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_current_user
from ..models import AiProject, User
from ..utils import strip_html
from .ai_factory import _SYSTEM, _chat_text, _get_project, _parse_json, _pick_config

router = APIRouter(prefix="/api/ai-factory", tags=["ai-factory"])

MAX_DECONSTRUCT_CHARS = 300_000
SAMPLE_CHARS = 60_000  # 送模型的采样长度（首 4 万 + 尾 2 万，兼顾开篇与后期节奏）

_REF_SCHEMA = """{
  "title": "参考书书名或题材标签（用户没提供可留空）",
  "worldview_framework": "世界观结构范式（如：宗门林立+位面晋升+血脉觉醒，≤150 字）",
  "power_system": "力量体系范式：等级阶梯、晋升方式、代价与限制（≤200 字）",
  "character_config": [{"role": "主角/对手/导师/女性角色/配角", "archetype": "人设原型（如 废柴逆袭/扮猪吃虎）", "traits": "具体特征与功能（≤80 字）"}],
  "pacing": "节奏与爽点分布：多少章一个小高潮、多少章一个大高潮、常见章节结构（≤200 字）",
  "hooks": ["开篇钩子模式", "章末钩子模式", "悬念维持手法"],
  "voice": "语言调性与叙事视角：句长、对白比例、描写密度、常用叙事技巧（≤150 字）",
  "avoid": ["这本书里已被用烂、新书应避免照搬的桥段或设定", "…"],
  "borrow_notes": "综合借鉴建议：新书该怎么借这些结构而不像抄袭（≤200 字）"
}"""

_DECONSTRUCT_PROMPT = """你在为一位网文作者做「拆书学习」——把参考书的**写法范式**提炼出来，供他写一本全新的书时借鉴。

参考书文本（可能已截断，按「书名/题材提示」优先）：
书名/题材提示：{title_hint}
----------------
{text}
----------------

请输出 JSON（只输出 JSON），字段如下：
{schema}

要求：
1. 提炼**可迁移的结构与套路**，不要复述具体情节；人名/地名/门派名一律不要出现在结果里。
2. hooks、avoid 各给 2-4 条，character_config 给 3-6 个角色位。
3. avoid 重点写：这本书已经用到泛滥、新书若照搬会被读者骂「又是这套」的桥段。
4. borrow_notes 要说清「借什么、换什么、怎么差异化」。
"""


class DeconstructIn(BaseModel):
    text: str = Field(min_length=200)
    title_hint: str = Field(default="", max_length=100)


class ReferenceIn(BaseModel):
    reference: dict


def _sample(text: str) -> str:
    """长文采样：首 4 万 + 尾 2 万（开篇定调与后期节奏都能看到）。"""
    plain = strip_html(text)
    if len(plain) <= SAMPLE_CHARS:
        return plain
    head = int(SAMPLE_CHARS * 2 / 3)
    tail = SAMPLE_CHARS - head
    return plain[:head] + "\n\n……（中间省略）……\n\n" + plain[-tail:]


@router.post("/deconstruct")
async def deconstruct_book(data: DeconstructIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """拆书学习：参考书文本 → 范式笔记 JSON（不落库，前端确认后可保存到项目）。"""
    text = data.text[:MAX_DECONSTRUCT_CHARS]
    if len(text.strip()) < 200:
        raise HTTPException(400, "参考书文本太短，至少粘 200 字（建议整本或前几万字）")
    config = await _pick_config(user, db, "")
    prompt = _DECONSTRUCT_PROMPT.format(title_hint=data.title_hint or "（未提供）", text=_sample(text), schema=_REF_SCHEMA)
    usage: dict = {}
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=4000, usage_sink=usage)
    try:
        ref = _parse_json(raw)
    except ValueError as e:
        raise HTTPException(502, f"AI 输出解析失败：{e}；原始输出前 200 字：{raw[:200]}") from e
    if not isinstance(ref, dict) or not (ref.get("worldview_framework") or ref.get("power_system")):
        raise HTTPException(502, "AI 输出缺少关键字段，请重试或换一段参考文本")
    # 用量回传（拆书发生在新项目创建前，无法落到项目账上；前端可展示本次成本）
    return {
        "reference": ref,
        "usage": {
            "model": config.model,
            "promptTokens": int(usage.get("prompt", 0) or 0),
            "completionTokens": int(usage.get("completion", 0) or 0),
        },
    }


@router.put("/projects/{project_id}/reference")
async def save_reference(project_id: int, data: ReferenceIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """保存（或人工编辑后覆盖）项目的范式笔记。"""
    p = await _get_project(project_id, user, db)
    p.reference_json = json.dumps(data.reference, ensure_ascii=False)[:20000]
    await db.commit()
    return {"ok": True, "reference": data.reference}


@router.delete("/projects/{project_id}/reference")
async def clear_reference(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """清空范式笔记（不再注入立项/设定）。"""
    p = await _get_project(project_id, user, db)
    p.reference_json = ""
    await db.commit()
    return {"ok": True}


def reference_prompt_block(p: AiProject) -> str:
    """把范式笔记渲染成注入立项/设定 prompt 的文本块（无笔记时返回空串）。"""
    if not p.reference_json:
        return ""
    try:
        ref = json.loads(p.reference_json)
    except (ValueError, TypeError):
        return ""
    if not isinstance(ref, dict):
        return ""
    lines: list[str] = []
    if ref.get("title"):
        lines.append(f"参考书：{ref['title']}")
    for key, label in (
        ("worldview_framework", "世界观结构范式"),
        ("power_system", "力量体系范式"),
        ("pacing", "节奏与爽点分布"),
        ("voice", "语言调性与视角"),
    ):
        if ref.get(key):
            lines.append(f"- {label}：{ref[key]}")
    chars = ref.get("character_config")
    if isinstance(chars, list) and chars:
        parts = []
        for c in chars[:6]:
            if isinstance(c, dict):
                parts.append(f"{c.get('role', '')}＝{c.get('archetype', '')}（{c.get('traits', '')}）")
        if parts:
            lines.append("- 人物配置范式：" + "；".join(parts))
    if isinstance(ref.get("hooks"), list) and ref["hooks"]:
        lines.append("- 钩子手法：" + "；".join(str(h) for h in ref["hooks"][:4]))
    if isinstance(ref.get("avoid"), list) and ref["avoid"]:
        lines.append("- 必须避开（参考书已用烂）：" + "；".join(str(a) for a in ref["avoid"][:4]))
    if ref.get("borrow_notes"):
        lines.append(f"- 借鉴建议：{ref['borrow_notes']}")
    if not lines:
        return ""
    return (
        "\n【拆书学习范式（借鉴结构与节奏，务必改头换面）】\n"
        + "\n".join(lines)
        + "\n要求：吸收上述结构、节奏与人物配置**思路**，但世界观名词、力量体系名称、人名地名势力名"
        "必须全新原创；avoid 里列出的桥段一律不要出现。\n"
    )
