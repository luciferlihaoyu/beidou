"""AI 工厂：从立项到正文的自动化写作流水线（与人工写作并行的第二体系）。

M1 范围：项目 CRUD + 立项（book_spec 八字段）+ 设定（角色卡/世界观入库）+ 大纲（卷章骨架）。
M2 再做逐章生成 / 审校 / 状态文件更新。

设计要点（docs/AI_FACTORY.md v2）：
- book_spec 八字段参考 GOAT：genre/time/place/theme/tone/pov/characters/premise
- AI 调用全部非流式 + 要求 JSON 输出（结构化结果好解析）
- 字数目标全可选，只注入 prompt 作软约束
- 立项确认时才建 Novel（标题 [AI] 前缀，与人工书区分）
"""

from __future__ import annotations

import json
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_ai_config, get_current_user
from ..models import AIConfig, AiChapterJob, AiProject, Chapter, Character, Novel, User, Volume, WorldviewEntry

router = APIRouter(prefix="/api/ai-factory", tags=["ai-factory"])


# ---------- AI 调用（非流式 + JSON 容错解析）----------


def _normalize_base(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


async def _pick_config(user: User, db: AsyncSession, route_field: str | None) -> AIConfig:
    """按任务路由取模型配置：route_field 存 AIConfig.id 字符串，None → 默认配置。"""
    if route_field:
        try:
            cfg_id = int(route_field)
        except ValueError:
            cfg_id = None
        if cfg_id is not None:
            config = await db.get(AIConfig, cfg_id)
            if config is not None and config.user_id == user.id and config.api_key:
                return config
    return await get_ai_config(user, db)


async def _chat_text(config: AIConfig, system: str, user_prompt: str, max_tokens: int = 4000) -> str:
    """非流式 chat completion，返回纯文本。"""
    url = _normalize_base(config.base_url) + "/v1/chat/completions"
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {config.api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise HTTPException(502, f"AI 接口返回 {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise HTTPException(502, f"AI 接口响应格式异常: {exc}") from exc


def _parse_json(text: str):
    """容错解析 AI 输出的 JSON：剥 ```json fence，截取首个 [/{ 到配对的最后 ]/}。"""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # 截取括号区间（数组或对象）
    start_arr, start_obj = t.find("["), t.find("{")
    if start_arr == -1 and start_obj == -1:
        raise ValueError("AI 输出中没有 JSON")
    if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
        end = t.rfind("]")
        if end == -1:
            raise ValueError("JSON 数组未闭合")
        t = t[start_arr : end + 1]
    else:
        end = t.rfind("}")
        if end == -1:
            raise ValueError("JSON 对象未闭合")
        t = t[start_obj : end + 1]
    return json.loads(t)


_SYSTEM = (
    "你是一位资深网文策划与作家，熟悉中文网文市场（起点/番茄/晋江）的题材、节奏与读者期待。"
    "输出务必直接、可用、具体，避免空话。要求 JSON 时只输出 JSON，不要任何解释。"
)


# ---------- 序列化 ----------


def _project_out(p: AiProject, novel: Novel | None = None, chapter_count: int = 0) -> dict:
    return {
        "id": p.id,
        "novel_id": p.novel_id,
        "novel_title": novel.title if novel else None,
        "status": p.status,
        "seed_prompt": p.seed_prompt,
        "book_spec": json.loads(p.book_spec_json) if p.book_spec_json else None,
        "genre": p.genre,
        "style_notes": p.style_notes,
        "target_total_words": p.target_total_words,
        "target_volume_words": p.target_volume_words,
        "target_chapter_words": p.target_chapter_words,
        "target_volumes": p.target_volumes,
        "target_chapters": p.target_chapters,
        "outline": json.loads(p.outline_json) if p.outline_json else None,
        "global_summary": p.global_summary,
        "auto_mode": p.auto_mode,
        "chapter_count": chapter_count,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


async def _get_project(project_id: int, user: User, db: AsyncSession) -> AiProject:
    p = await db.get(AiProject, project_id)
    if p is None or p.user_id != user.id:
        raise HTTPException(404, "项目不存在")
    return p


# ---------- 项目 CRUD ----------


class ProjectIn(BaseModel):
    seed_prompt: str = Field(min_length=4, max_length=2000)
    genre: str = Field(default="", max_length=50)
    style_notes: str = Field(default="", max_length=1000)
    # 字数目标（全可选）
    target_total_words: int | None = Field(default=None, ge=10000, le=10000000)
    target_volumes: int | None = Field(default=None, ge=1, le=50)
    target_chapters: int | None = Field(default=None, ge=1, le=2000)
    target_volume_words: int | None = Field(default=None, ge=1000, le=1000000)
    target_chapter_words: int | None = Field(default=None, ge=200, le=20000)


@router.post("/projects")
async def create_project(data: ProjectIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = AiProject(
        user_id=user.id,
        seed_prompt=data.seed_prompt.strip(),
        genre=data.genre.strip(),
        style_notes=data.style_notes.strip(),
        target_total_words=data.target_total_words,
        target_volumes=data.target_volumes,
        target_chapters=data.target_chapters,
        target_volume_words=data.target_volume_words,
        target_chapter_words=data.target_chapter_words,
    )
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _project_out(p)


@router.get("/projects")
async def list_projects(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    projects = (
        (await db.execute(select(AiProject).where(AiProject.user_id == user.id).order_by(AiProject.updated_at.desc())))
        .scalars()
        .all()
    )
    out = []
    for p in projects:
        novel = await db.get(Novel, p.novel_id) if p.novel_id else None
        cc = 0
        if p.novel_id:
            cc = (
                await db.execute(select(func.count(Chapter.id)).where(Chapter.novel_id == p.novel_id))
            ).scalar_one()
        out.append(_project_out(p, novel, cc))
    return out


@router.get("/projects/{project_id}")
async def get_project(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    p = await _get_project(project_id, user, db)
    novel = await db.get(Novel, p.novel_id) if p.novel_id else None
    cc = 0
    if p.novel_id:
        cc = (await db.execute(select(func.count(Chapter.id)).where(Chapter.novel_id == p.novel_id))).scalar_one()
    return _project_out(p, novel, cc)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    delete_novel: bool = False,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除 AI 项目；delete_novel=true 时连带删除生成的小说（默认保留，小说转人工书）。"""
    p = await _get_project(project_id, user, db)
    novel_id = p.novel_id
    await db.delete(p)
    if delete_novel and novel_id:
        novel = await db.get(Novel, novel_id)
        if novel is not None and novel.user_id == user.id:
            await db.delete(novel)
    elif novel_id:
        # 保留小说：去掉 [AI] 前缀，转为人工书
        novel = await db.get(Novel, novel_id)
        if novel is not None and novel.title.startswith("[AI] "):
            novel.title = novel.title[5:]
    await db.commit()
    return {"ok": True}


# ---------- 阶段 1：立项（book_spec 八字段，参考 GOAT）----------


@router.post("/projects/{project_id}/init")
async def init_project(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """AI 生成立项草案：book_spec 八字段 + 书名候选 ×3。"""
    p = await _get_project(project_id, user, db)
    if p.status not in ("draft",):
        raise HTTPException(400, f"当前状态 {p.status} 不能重新立项")
    config = await _pick_config(user, db, p.setup_llm)

    targets = []
    if p.target_total_words:
        targets.append(f"总字数目标约 {p.target_total_words} 字")
    if p.target_chapters:
        targets.append(f"计划约 {p.target_chapters} 章")
    if p.target_chapter_words:
        targets.append(f"单章约 {p.target_chapter_words} 字")
    target_line = ("篇幅约束：" + "，".join(targets) + "。\n") if targets else ""

    prompt = (
        f"用户的一句话创意：{p.seed_prompt}\n"
        + (f"类型偏好：{p.genre}\n" if p.genre else "")
        + (f"风格要求：{p.style_notes}\n" if p.style_notes else "")
        + target_line
        + "\n请为这个创意做小说立项，输出 JSON（只输出 JSON）：\n"
        "{\n"
        '  "titles": ["书名候选1", "书名候选2", "书名候选3"],\n'
        '  "genre": "类型（如 玄幻/都市/科幻/言情/悬疑）",\n'
        '  "time": "时代背景",\n'
        '  "place": "主要地点/世界",\n'
        '  "theme": "核心主题（一句话）",\n'
        '  "tone": "基调（如 热血/轻松/黑暗/治愈）",\n'
        '  "pov": "叙事视角（如 第三人称限制视角-主角）",\n'
        '  "characters": [{"name": "主角名", "role": "主角", "brief": "一句话人设"}],\n'
        '  "premise": "核心梗概（150 字内：主角是谁、要什么、最大阻碍）"\n'
        "}"
    )
    raw = await _chat_text(config, _SYSTEM, prompt)
    try:
        spec = _parse_json(raw)
    except ValueError as e:
        raise HTTPException(502, f"AI 输出解析失败：{e}；原始输出前 200 字：{raw[:200]}") from e
    if not isinstance(spec, dict) or not spec.get("premise"):
        raise HTTPException(502, "AI 输出缺少核心字段（premise）")
    p.book_spec_json = json.dumps(spec, ensure_ascii=False)
    if spec.get("genre") and not p.genre:
        p.genre = str(spec["genre"])[:50]
    await db.commit()
    return _project_out(p)


class BookSpecConfirmIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)  # 最终选定的书名
    book_spec: dict  # 人工可改后的八字段


@router.put("/projects/{project_id}/book-spec")
async def confirm_book_spec(
    project_id: int, data: BookSpecConfirmIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """确认立项：保存 book_spec + 建 Novel（[AI] 前缀），状态推进到 setup。"""
    p = await _get_project(project_id, user, db)
    if p.status not in ("draft", "setup"):
        raise HTTPException(400, f"当前状态 {p.status} 不能修改立项")
    p.book_spec_json = json.dumps(data.book_spec, ensure_ascii=False)
    if p.novel_id is None:
        novel = Novel(
            user_id=user.id,
            title=f"[AI] {data.title.strip()}",
            genre=p.genre or str(data.book_spec.get("genre", ""))[:50],
            description=str(data.book_spec.get("premise", "")),
        )
        db.add(novel)
        await db.flush()
        p.novel_id = novel.id
    else:
        novel = await db.get(Novel, p.novel_id)
        if novel is not None:
            novel.title = f"[AI] {data.title.strip()}"
            novel.description = str(data.book_spec.get("premise", ""))
    p.status = "setup"
    await db.commit()
    novel = await db.get(Novel, p.novel_id) if p.novel_id else None
    return _project_out(p, novel)


# ---------- 阶段 2：设定（角色卡 + 世界观入库）----------


@router.post("/projects/{project_id}/setup")
async def setup_project(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """AI 生成角色卡 + 世界观条目，写入现有 Character / WorldviewEntry 表。"""
    p = await _get_project(project_id, user, db)
    if p.status not in ("setup",):
        raise HTTPException(400, f"当前状态 {p.status} 不能生成设定（请先完成立项确认）")
    if p.novel_id is None:
        raise HTTPException(400, "项目尚未关联小说（请先确认立项）")
    spec = json.loads(p.book_spec_json or "{}")
    config = await _pick_config(user, db, p.setup_llm)

    prompt = (
        f"小说立项信息：\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n\n"
        "请基于立项生成完整设定，输出 JSON（只输出 JSON）：\n"
        "{\n"
        '  "characters": [\n'
        "    {\"name\": \"角色名\", \"role\": \"主角/配角/反派/导师\", "
        '"description": "外貌+性格+动机+口癖（100 字内）", "arc": "角色弧光/成长线（50 字内）"}\n'
        "  ],\n"
        '  "worldview": [\n'
        "    {\"category\": " '"力量体系/地理/势力/历史/物品/规则"' ", "
        '"title": "条目标题", "content": "条目内容（150 字内）"}\n'
        "  ]\n"
        "}\n"
        "要求：角色 4-8 个（主角必须第一个，含至少 1 个核心对手）；世界观 6-12 条覆盖力量体系与核心规则；"
        "所有设定必须服务于 premise 的核心冲突。"
    )
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=6000)
    try:
        data = _parse_json(raw)
    except ValueError as e:
        raise HTTPException(502, f"AI 输出解析失败：{e}") from e

    chars = data.get("characters") or []
    world = data.get("worldview") or []
    if not chars:
        raise HTTPException(502, "AI 未生成角色")

    # 清掉旧的 AI 生成设定（重新生成场景），保留人工加过的？v1：setup 阶段整体重建
    old_chars = (
        (await db.execute(select(Character).where(Character.novel_id == p.novel_id))).scalars().all()
    )
    for c in old_chars:
        await db.delete(c)
    old_world = (
        (await db.execute(select(WorldviewEntry).where(WorldviewEntry.novel_id == p.novel_id))).scalars().all()
    )
    for w in old_world:
        await db.delete(w)

    for c in chars[:12]:
        db.add(
            Character(
                novel_id=p.novel_id,
                name=str(c.get("name", ""))[:100],
                role=str(c.get("role", "配角"))[:50],
                description=str(c.get("description", ""))[:2000]
                + (f"\n弧光：{c.get('arc')}" if c.get("arc") else ""),
            )
        )
    for w in world[:20]:
        db.add(
            WorldviewEntry(
                novel_id=p.novel_id,
                category=str(w.get("category", "其他"))[:50],
                title=str(w.get("title", ""))[:200],
                content=str(w.get("content", ""))[:5000],
            )
        )
    p.status = "outline"
    await db.commit()
    novel = await db.get(Novel, p.novel_id)
    return _project_out(p, novel)


# ---------- 阶段 3：大纲（卷-章骨架 + 每章剧情要点）----------


@router.post("/projects/{project_id}/outline")
async def outline_project(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """AI 生成卷-章大纲：建 Volume/Chapter 骨架 + AiChapterJob（pending）。"""
    p = await _get_project(project_id, user, db)
    if p.status not in ("outline",):
        raise HTTPException(400, f"当前状态 {p.status} 不能生成大纲（请先完成设定）")
    spec = json.loads(p.book_spec_json or "{}")
    config = await _pick_config(user, db, p.outline_llm)

    chars = (
        (await db.execute(select(Character).where(Character.novel_id == p.novel_id))).scalars().all()
    )
    char_line = "；".join(f"{c.name}（{c.role}）" for c in chars[:10])

    n_chapters = p.target_chapters or 30
    n_volumes = p.target_volumes or 3
    cw = p.target_chapter_words or 3000

    prompt = (
        f"小说立项：\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n"
        f"角色：{char_line}\n"
        + (f"风格：{p.style_notes}\n" if p.style_notes else "")
        + f"\n请设计全书大纲：共 {n_volumes} 卷、约 {n_chapters} 章（每章目标约 {cw} 字，仅作节奏参考）。\n"
        "输出 JSON（只输出 JSON）：\n"
        "{\n"
        '  "volumes": [\n'
        "    {\n"
        '      "title": "第一卷 卷名",\n'
        '      "summary": "本卷主线（50 字内）",\n'
        '      "chapters": [{"title": "章名（不含第几章）", "outline": "本章剧情要点+出场角色+情绪目标（80 字内）"}]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "要求：三章一小高潮、一卷一大高潮；第一卷黄金三章必须亮出核心冲突与金手指；"
        "每章 outline 要具体到事件，不写空话。"
    )
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=8000)
    try:
        data = _parse_json(raw)
    except ValueError as e:
        raise HTTPException(502, f"AI 输出解析失败：{e}") from e
    volumes_data = data.get("volumes") or []
    if not volumes_data:
        raise HTTPException(502, "AI 未生成大纲")

    # 重建卷章骨架（outline 阶段允许重新生成）
    old_chapters = (
        (await db.execute(select(Chapter).where(Chapter.novel_id == p.novel_id))).scalars().all()
    )
    for c in old_chapters:
        await db.delete(c)
    old_volumes = (
        (await db.execute(select(Volume).where(Volume.novel_id == p.novel_id))).scalars().all()
    )
    for v in old_volumes:
        await db.delete(v)
    old_jobs = (
        (await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id))).scalars().all()
    )
    for j in old_jobs:
        await db.delete(j)
    await db.flush()

    total_chapters = 0
    for vi, v in enumerate(volumes_data[:20]):
        volume = Volume(novel_id=p.novel_id, title=str(v.get("title", f"第{vi+1}卷"))[:200], sort_order=vi)
        db.add(volume)
        await db.flush()
        for ci, ch in enumerate((v.get("chapters") or [])[:100]):
            chapter = Chapter(
                novel_id=p.novel_id,
                volume_id=volume.id,
                title=str(ch.get("title", ""))[:200],
                content="",
                sort_order=ci,
                status="draft",
            )
            db.add(chapter)
            await db.flush()
            db.add(
                AiChapterJob(
                    project_id=p.id,
                    chapter_id=chapter.id,
                    status="pending",
                    outline=str(ch.get("outline", ""))[:2000],
                )
            )
            total_chapters += 1

    p.outline_json = json.dumps(data, ensure_ascii=False)
    p.status = "writing"
    await db.commit()
    novel = await db.get(Novel, p.novel_id)
    return _project_out(p, novel, total_chapters)
