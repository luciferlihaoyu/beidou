"""人物关系（C 级：人物关系图）：手动增删 + AI 从角色卡/正文自动抽取。"""

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_ai_config, get_current_user, get_owned_novel
from ..models import Chapter, Character, CharacterRelation, Novel, User, Volume
from ..utils import order_chapters, strip_html
from .ai import SYSTEM_PROMPT, _normalize_base

router = APIRouter(prefix="/api/novels/{novel_id}/relations", tags=["relations"])


# ---------- 序列化 ----------

class RelationIn(BaseModel):
    from_character_id: int
    to_character_id: int
    relation: str = Field(min_length=1, max_length=50)
    description: str = Field(default="", max_length=300)


class RelationOut(BaseModel):
    id: int
    from_character_id: int
    to_character_id: int
    from_name: str  # 起点角色名（列表时联表带出，前端免二次查询）
    to_name: str
    relation: str
    description: str
    source: str  # manual / ai


def _to_out(rel: CharacterRelation, names: dict[int, str]) -> RelationOut:
    return RelationOut(
        id=rel.id,
        from_character_id=rel.from_character_id,
        to_character_id=rel.to_character_id,
        from_name=names.get(rel.from_character_id, "已删除角色"),
        to_name=names.get(rel.to_character_id, "已删除角色"),
        relation=rel.relation,
        description=rel.description,
        source=rel.source,
    )


async def _novel_characters(novel_id: int, db: AsyncSession) -> list[Character]:
    result = await db.execute(
        select(Character).where(Character.novel_id == novel_id).order_by(Character.id)
    )
    return list(result.scalars().all())


# ---------- 列表 / 新增 / 删除 ----------

@router.get("", response_model=list[RelationOut])
async def list_relations(novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)):
    chars = await _novel_characters(novel.id, db)
    names = {c.id: c.name for c in chars}
    rows = (
        await db.execute(
            select(CharacterRelation)
            .where(CharacterRelation.novel_id == novel.id)
            .order_by(CharacterRelation.id)
        )
    ).scalars().all()
    return [_to_out(r, names) for r in rows]


@router.post("", response_model=RelationOut)
async def create_relation(
    data: RelationIn, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    if data.from_character_id == data.to_character_id:
        raise HTTPException(400, "起点与终点不能是同一个角色")
    chars = await _novel_characters(novel.id, db)
    names = {c.id: c.name for c in chars}
    if data.from_character_id not in names or data.to_character_id not in names:
        raise HTTPException(400, "角色不存在或不属于本书")
    rel = CharacterRelation(
        novel_id=novel.id,
        from_character_id=data.from_character_id,
        to_character_id=data.to_character_id,
        relation=data.relation.strip(),
        description=data.description.strip(),
        source="manual",
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)
    return _to_out(rel, names)


@router.delete("/{relation_id}")
async def delete_relation(
    relation_id: int, novel: Novel = Depends(get_owned_novel), db: AsyncSession = Depends(get_db)
):
    rel = await db.get(CharacterRelation, relation_id)
    if rel is None or rel.novel_id != novel.id:
        raise HTTPException(404, "关系不存在")
    await db.delete(rel)
    await db.commit()
    return {"ok": True}


# ---------- AI 抽取 ----------

def _parse_relations_json(text: str) -> list[dict]:
    """容错解析 AI 输出：剥离 ```json 围栏，截取首个 [ 到最后一个 ]，要求为对象数组。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # 去掉首行 ```json / ``` 与结尾 ```
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


@router.post("/extract")
async def extract_relations(
    novel: Novel = Depends(get_owned_novel),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI 抽取人物关系：角色卡 + 最近 3 章正文节选 → 严格 JSON → 去重入库。

    返回 {created, skipped}：skipped 含「已存在的 from+to+relation」与「角色名无法匹配」。
    """
    chars = await _novel_characters(novel.id, db)
    if len(chars) < 2:
        raise HTTPException(400, "至少需要 2 个角色卡才能抽取关系，请先在「角色」页创建")
    config = await get_ai_config(user, db)

    # 最近 3 章正文节选（每章末尾 1500 字，关系往往在章节后段的对手戏里体现）
    chapters = (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    volumes = (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    ordered = order_chapters(chapters, volumes)
    recent = [c for c in ordered if strip_html(c.content).strip()][-3:]

    char_cards = "\n".join(
        f"- {c.name}（{c.role}）：{(c.description or '')[:150]}"
        + (f"；已知关系：{c.relations[:100]}" if c.relations else "")
        for c in chars
    )
    chapter_parts = []
    for ch in recent:
        text = strip_html(ch.content).strip()
        chapter_parts.append(f"【{ch.title or '未命名章节'}】（节选结尾）：\n{text[-1500:]}")
    chapters_text = "\n\n".join(chapter_parts) if chapter_parts else "（暂无正文）"

    prompt = (
        f"作品：《{novel.title}》\n\n"
        f"角色卡列表：\n{char_cards}\n\n"
        f"最近章节正文节选：\n{chapters_text}\n\n"
        "请根据以上角色卡与正文，抽取角色之间的关系。"
        "只输出一个严格的 JSON 数组，不要输出任何其他文字、解释或 Markdown 围栏，格式：\n"
        '[{"from": "角色名", "to": "角色名", "relation": "关系", "description": "一句话简述"}]\n'
        "要求：from/to 必须使用角色卡列表中的原名；relation 用简短中文词（如 师徒/仇敌/兄妹/夫妻/主仆/挚友）；"
        "关系是有方向的（from 对 to 的关系）；最多输出 15 条；没有把握的关系不要输出。"
    )
    url = _normalize_base(config.base_url) + "/v1/chat/completions"
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.3,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            resp = await client.post(
                url, json=payload, headers={"Authorization": f"Bearer {config.api_key}"}
            )
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接 AI 接口: {exc.__class__.__name__}")
    if resp.status_code != 200:
        raise HTTPException(400, f"AI 接口返回 {resp.status_code}: {resp.text[:200]}")
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise HTTPException(400, "AI 接口返回格式异常")

    items = _parse_relations_json(content)
    if not items:
        raise HTTPException(400, "AI 未能输出可解析的关系 JSON，请重试或检查模型输出")

    # 角色名模糊匹配：去空格后相等
    name_map: dict[str, Character] = {}
    for c in chars:
        name_map.setdefault(c.name.replace(" ", "").replace("　", ""), c)

    # 已存在的 from+to+relation 组合（跳过重复入库）
    existing_rows = (
        await db.execute(select(CharacterRelation).where(CharacterRelation.novel_id == novel.id))
    ).scalars().all()
    seen = {(r.from_character_id, r.to_character_id, r.relation) for r in existing_rows}

    created = 0
    skipped = 0
    for item in items:
        from_name = str(item.get("from", "")).replace(" ", "").replace("　", "")
        to_name = str(item.get("to", "")).replace(" ", "").replace("　", "")
        relation = str(item.get("relation", "")).strip()[:50]
        description = str(item.get("description", "")).strip()[:300]
        from_char = name_map.get(from_name)
        to_char = name_map.get(to_name)
        if not from_char or not to_char or from_char.id == to_char.id or not relation:
            skipped += 1
            continue
        key = (from_char.id, to_char.id, relation)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        db.add(
            CharacterRelation(
                novel_id=novel.id,
                from_character_id=from_char.id,
                to_character_id=to_char.id,
                relation=relation,
                description=description,
                source="ai",
            )
        )
        created += 1
    await db.commit()
    return {"created": created, "skipped": skipped}
