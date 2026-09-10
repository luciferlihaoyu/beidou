"""AI 工厂：从立项到正文的自动化写作流水线（与人工写作并行的第二体系）。

M1：项目 CRUD + 立项（book_spec 八字段）+ 设定（角色卡/世界观入库）+ 大纲（卷章骨架）。
M2：逐章生成（SSE 流式，上下文预算制组装）+ 定稿（状态文件四分 AI 增量更新）+
    一致性审校（可选按钮）+ 项目设置（auto_mode / 上下文窗口 / 模型路由）。

设计要点（docs/AI_FACTORY.md v2）：
- book_spec 八字段参考 GOAT：genre/time/place/theme/tone/pov/characters/premise
- 状态文件四分（AI_NovelGenerator 实证）：global_summary/character_state/plot_arcs + FTS 召回
- 立项/设定/大纲用非流式 + JSON；正文生成用 SSE 流式（用户体验）
- 字数目标全可选，只注入 prompt 作软约束（±20% 浮动，剧情完整优先，绝不截断）
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
    """按任务路由取模型配置。

    route_field 格式：
    - None / ""        → 默认配置
    - "3"              → AIConfig id=3（用配置的默认模型）
    - "3@deepseek-v4"  → AIConfig id=3 但模型覆盖为 deepseek-v4
      （模型名来自 GET /api/ai/configs/{id}/models 拉到的端点清单）

    注意：覆盖模型时必须构造游离 AIConfig 副本——直接改 db.get 出来的托管
    实例会被后续 db.commit() 写回数据库（脏数据事故）。
    """
    if route_field:
        cfg_id_str, _, model_override = route_field.partition("@")
        try:
            cfg_id = int(cfg_id_str)
        except ValueError:
            cfg_id = None
        if cfg_id is not None:
            config = await db.get(AIConfig, cfg_id)
            if config is not None and config.user_id == user.id and config.api_key:
                if model_override:
                    return AIConfig(
                        user_id=config.user_id,
                        name=config.name,
                        base_url=config.base_url,
                        api_key=config.api_key,
                        model=model_override[:100],
                        is_default=False,
                    )
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
        "author_intent": p.author_intent,
        "current_focus": p.current_focus,
        "particle_ledger": p.particle_ledger,
        "subplot_board": p.subplot_board,
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


# ==================== M2：逐章生成 / 定稿 / 审校 ====================

from ..deps import count_words, get_owned_novel  # noqa: E402
from ..utils import chapter_display_title, order_chapters, strip_html  # noqa: E402


def _job_out(job: AiChapterJob, chapter: Chapter | None, number: int) -> dict:
    return {
        "id": job.id,
        "chapter_id": job.chapter_id,
        "chapter_title": chapter_display_title(chapter.title, number) if chapter else "（已删除）",
        "status": job.status,
        "outline": job.outline,
        "actual_words": job.actual_words,
        "attempt": job.attempt,
        "review_issues": json.loads(job.review_issues) if job.review_issues else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/projects/{project_id}/jobs")
async def list_jobs(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """章节任务列表（按显示顺序带章节号）。"""
    p = await _get_project(project_id, user, db)
    jobs = (
        (await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id)))
        .scalars()
        .all()
    )
    if p.novel_id is None:
        return []
    chapters = (
        (await db.execute(select(Chapter).where(Chapter.novel_id == p.novel_id))).scalars().all()
    )
    volumes = (
        (await db.execute(select(Volume).where(Volume.novel_id == p.novel_id))).scalars().all()
    )
    ordered = order_chapters(chapters, volumes)
    number_map = {c.id: i + 1 for i, c in enumerate(ordered)}
    chapter_map = {c.id: c for c in chapters}
    # 按章节显示顺序排
    jobs.sort(key=lambda j: number_map.get(j.chapter_id or 0, 99999))
    return [_job_out(j, chapter_map.get(j.chapter_id), number_map.get(j.chapter_id or 0, 0)) for j in jobs]


async def _assemble_context(p: AiProject, novel: Novel, chapter: Chapter, job: AiChapterJob, db: AsyncSession) -> str:
    """上下文预算制组装（§3.1）：立项 + 状态文件三件套 + 角色卡 + 最近 N 章 + 本章任务。"""
    parts = [f"作品：《{novel.title}》"]
    spec = json.loads(p.book_spec_json or "{}")
    if spec:
        brief = {k: spec.get(k) for k in ("genre", "time", "place", "theme", "tone", "pov", "premise") if spec.get(k)}
        parts.append("立项设定：" + json.dumps(brief, ensure_ascii=False))
    if p.style_notes:
        parts.append(f"风格要求：{p.style_notes}")

    # 角色卡（全量，≤10 个角色各 150 字）
    chars = (
        (await db.execute(select(Character).where(Character.novel_id == novel.id).limit(10)))
        .scalars()
        .all()
    )
    if chars:
        parts.append("角色卡：" + "；".join(f"{c.name}（{c.role}）：{(c.description or '')[:150]}" for c in chars))

    # 状态文件三件套（每章定稿后更新）
    if p.global_summary:
        parts.append(f"前情摘要：{p.global_summary}")
    if p.character_state:
        parts.append(f"角色当前状态：{p.character_state}")
    if p.plot_arcs:
        parts.append(f"伏笔台账：{p.plot_arcs}")

    # 最近 N 章原文（默认 2 章，每章取末尾 2500 字——接最近的情节）
    chapters = (
        (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    )
    volumes = (
        (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    )
    ordered = order_chapters(chapters, volumes)
    idx = next((i for i, c in enumerate(ordered) if c.id == chapter.id), None)
    if idx is not None and idx > 0:
        n_recent = max(1, p.context_recent_chapters)
        recent = [c for c in ordered[:idx] if strip_html(c.content).strip()][-n_recent:]
        # 用户手动加选的章节（91Writing 模式）
        try:
            extra_ids = {int(x) for x in json.loads(p.context_extra_chapters or "[]")}
        except (ValueError, TypeError):
            extra_ids = set()
        extra = [c for c in ordered[:idx] if c.id in extra_ids and c not in recent]
        for c in extra + recent:
            number = next((i + 1 for i, x in enumerate(ordered) if x.id == c.id), 0)
            text = strip_html(c.content).strip()
            parts.append(f"【{chapter_display_title(c.title, number)}】正文（节选结尾）：\n{text[-2500:]}")

    # 本章任务
    number = next((i + 1 for i, c in enumerate(ordered) if c.id == chapter.id), 0)
    task = f"现在请写{chapter_display_title(chapter.title, number)}。"
    if job.outline:
        task += f"\n本章大纲：{job.outline}"
    if p.target_chapter_words:
        task += (
            f"\n本章目标约 {p.target_chapter_words} 字（±20% 浮动，剧情完整优先，"
            "绝不在情节中段强行收尾）。"
        )
    task += "\n直接输出正文（纯文本，段落之间空一行），不要输出章节标题、不要任何解释。"
    parts.append(task)
    return "\n\n".join(parts)


def _text_to_html(text: str) -> str:
    """AI 输出纯文本 → Tiptap HTML（空行分段，段内换行转 <br>）。"""
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    return "".join(
        f"<p>{esc(p.strip()).replace(chr(10), '<br>')}</p>"
        for p in re.split(r"\n{2,}", text)
        if p.strip()
    )


@router.post("/projects/{project_id}/jobs/{job_id}/generate")
async def generate_chapter(
    project_id: int, job_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """SSE 流式生成一章正文。job → writing；内容由客户端收集后走 finalize 落库。"""
    p = await _get_project(project_id, user, db)
    if p.status != "writing":
        raise HTTPException(400, f"当前状态 {p.status} 不能生成正文（请先完成大纲）")
    if p.novel_id is None:
        raise HTTPException(400, "项目未关联小说")
    job = await db.get(AiChapterJob, job_id)
    if job is None or job.project_id != p.id or job.chapter_id is None:
        raise HTTPException(404, "章节任务不存在")
    chapter = await db.get(Chapter, job.chapter_id)
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    novel = await db.get(Novel, p.novel_id)
    assert novel is not None

    config = await _pick_config(user, db, p.chapter_llm)
    context = await _assemble_context(p, novel, chapter, job, db)

    job.status = "writing"
    job.attempt += 1
    await db.commit()

    from .ai import _stream_openai

    system = (
        _SYSTEM
        + "你正在执行整章正文写作任务。要求：中文网文风格，段落短小（手机阅读友好），"
        "对话生动，章末留钩子。严格遵守角色卡与状态文件的一致性。"
    )
    return await _stream_openai(config, [{"role": "system", "content": system}, {"role": "user", "content": context}])


class FinalizeIn(BaseModel):
    content_text: str = Field(min_length=20)  # 客户端收集的 AI 纯文本输出


@router.post("/projects/{project_id}/jobs/{job_id}/finalize")
async def finalize_chapter(
    project_id: int,
    job_id: int,
    data: FinalizeIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """定稿：正文写入章节 + 更新状态文件四分（AI 增量总结）+ job done。

    状态文件更新失败不阻塞定稿（正文已落库），返回里带 state_updated 标记。
    """
    p = await _get_project(project_id, user, db)
    if p.novel_id is None:
        raise HTTPException(400, "项目未关联小说")
    job = await db.get(AiChapterJob, job_id)
    if job is None or job.project_id != p.id or job.chapter_id is None:
        raise HTTPException(404, "章节任务不存在")
    chapter = await db.get(Chapter, job.chapter_id)
    if chapter is None:
        raise HTTPException(404, "章节不存在")

    # 1. 正文落库（手写 HTML 转换 + 字数统计 + FTS 同步）
    chapter.content = _text_to_html(data.content_text)
    chapter.word_count = count_words(chapter.content)
    if chapter.status == "draft":
        chapter.status = "writing"
    await db.commit()
    try:
        from ..search_fts import sync_chapter

        await sync_chapter(db, chapter.id)
    except Exception:  # noqa: BLE001
        pass

    # 2. AI 更新状态文件四分（global_summary / character_state / plot_arcs）
    state_updated = False
    try:
        config = await _pick_config(user, db, p.summary_llm)
        update_prompt = (
            f"作品：《{(await db.get(Novel, p.novel_id)).title}》\n\n"
            f"【现有前情摘要】\n{p.global_summary or '（空——这是第一章）'}\n\n"
            f"【现有角色状态】\n{p.character_state or '（空）'}\n\n"
            f"【现有伏笔台账】\n{p.plot_arcs or '（空）'}\n\n"
            f"【刚完成的本章《{chapter.title or ''}》正文】\n{strip_html(chapter.content)[:4000]}\n\n"
            "请增量更新三份状态文件，输出 JSON（只输出 JSON）：\n"
            "{\n"
            '  "global_summary": "融合本章后的全书摘要（≤1200 字，保留旧关键情节，补本章进展）",\n'
            '  "character_state": [{"name": "角色名", "location": "所在", "goal": "当前目标", '
            '"condition": "身体/情绪状态", "change": "本章变化"}],\n'
            '  "plot_arcs": [{"title": "伏笔/情节弧", "status": "埋设中/推进中/已回收", '
            '"note": "本章进展"}]\n'
            "}\n"
            "要求：只列活跃角色与未回收伏笔；已回收的伏笔保留一条标记已回收；"
            "状态文件是给后续章节生成用的记忆，要精炼、结构化。"
        )
        raw = await _chat_text(config, _SYSTEM, update_prompt, max_tokens=3000)
        state = _parse_json(raw)
        if isinstance(state, dict):
            if state.get("global_summary"):
                p.global_summary = str(state["global_summary"])[:3000]
            if isinstance(state.get("character_state"), list):
                p.character_state = json.dumps(state["character_state"], ensure_ascii=False)[:4000]
            if isinstance(state.get("plot_arcs"), list):
                p.plot_arcs = json.dumps(state["plot_arcs"], ensure_ascii=False)[:4000]
            state_updated = True
    except Exception:  # noqa: BLE001  状态文件更新失败不阻塞定稿
        pass

    # 3. 本章摘要 + job 状态
    try:
        config = await _pick_config(user, db, p.summary_llm)
        job.summary = (
            await _chat_text(
                config, _SYSTEM, f"把以下章节正文压缩成 150 字剧情摘要（只输出摘要）：\n{strip_html(chapter.content)[:4000]}", max_tokens=400
            )
        ).strip()[:500]
    except Exception:  # noqa: BLE001
        pass

    from datetime import datetime, timezone

    job.status = "done"
    job.actual_words = chapter.word_count
    job.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "word_count": chapter.word_count, "state_updated": state_updated}


@router.post("/projects/{project_id}/jobs/{job_id}/review")
async def review_chapter(
    project_id: int, job_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """一致性审校（可选按钮，AI_NovelGenerator 模式）：设定冲突/前文矛盾/角色状态/字数偏离。"""
    p = await _get_project(project_id, user, db)
    if p.novel_id is None:
        raise HTTPException(400, "项目未关联小说")
    job = await db.get(AiChapterJob, job_id)
    if job is None or job.project_id != p.id or job.chapter_id is None:
        raise HTTPException(404, "章节任务不存在")
    chapter = await db.get(Chapter, job.chapter_id)
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    text = strip_html(chapter.content).strip()
    if len(text) < 20:
        raise HTTPException(400, "章节还没有正文，无法审校")

    config = await _pick_config(user, db, p.review_llm)
    novel = await db.get(Novel, p.novel_id)
    spec = json.loads(p.book_spec_json or "{}")
    chars = (
        (await db.execute(select(Character).where(Character.novel_id == p.novel_id).limit(10)))
        .scalars()
        .all()
    )
    char_line = "；".join(f"{c.name}（{c.role}）：{(c.description or '')[:100]}" for c in chars)
    word_note = ""
    if p.target_chapter_words:
        dev = abs(chapter.word_count - p.target_chapter_words) / p.target_chapter_words
        if dev > 0.5:
            word_note = f"（注意：本章 {chapter.word_count} 字，偏离目标 {p.target_chapter_words} 字超 50%）"

    prompt = (
        f"作品：《{novel.title}》\n立项：{json.dumps(spec, ensure_ascii=False)[:800]}\n"
        f"角色卡：{char_line}\n"
        + (f"前情摘要：{p.global_summary}\n" if p.global_summary else "")
        + (f"角色状态：{p.character_state}\n" if p.character_state else "")
        + (f"伏笔台账：{p.plot_arcs}\n" if p.plot_arcs else "")
        + f"\n【待审校章节《{chapter.title or ''}》】{word_note}\n{text[:6000]}\n\n"
        "请审校本章，检查：①与立项/角色卡冲突 ②与前情摘要矛盾 ③角色状态不一致 "
        "④伏笔脱节 ⑤字数偏离（若标注了偏离）。\n"
        '输出 JSON 数组（只输出 JSON）：[{"type": "冲突|矛盾|状态|伏笔|字数", '
        '"severity": "high|low", "issue": "问题描述", "suggestion": "修改建议"}]\n'
        "没有问题就输出空数组 []。"
    )
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=3000)
    try:
        issues = _parse_json(raw)
        if not isinstance(issues, list):
            issues = []
    except ValueError:
        issues = []
    job.review_issues = json.dumps(issues, ensure_ascii=False)
    has_high = any(i.get("severity") == "high" for i in issues if isinstance(i, dict))
    if has_high and job.status == "done":
        job.status = "needs_fix"
    await db.commit()
    return {"issues": issues, "has_high": has_high}


# ---------- 项目设置（auto_mode / 上下文窗口 / 模型路由）----------


class ProjectSettingsIn(BaseModel):
    auto_mode: bool | None = None
    author_intent: str | None = Field(default=None, max_length=2000)
    current_focus: str | None = Field(default=None, max_length=2000)
    context_recent_chapters: int | None = Field(default=None, ge=1, le=10)
    context_extra_chapters: list[int] | None = None
    setup_llm: str | None = None
    outline_llm: str | None = None
    chapter_llm: str | None = None
    summary_llm: str | None = None
    review_llm: str | None = None
    target_chapter_words: int | None = Field(default=None, ge=200, le=20000)


@router.put("/projects/{project_id}")
async def update_project(
    project_id: int, data: ProjectSettingsIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """更新项目设置（全部可选，传什么改什么）。"""
    p = await _get_project(project_id, user, db)
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "context_extra_chapters":
            setattr(p, field, json.dumps(value or []))
        else:
            setattr(p, field, value)
    await db.commit()
    novel = await db.get(Novel, p.novel_id) if p.novel_id else None
    return _project_out(p, novel)
