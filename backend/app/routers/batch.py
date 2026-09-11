"""AI 工厂 M3：批量连跑 + 28 维增强审校 + 追读力（借鉴上官婉儿分享的
webnovel-master（美智子作品，源自 inkos）的 reviewer 循环修订 / retention / daemon 设计）。

端点：
- POST /projects/{id}/batch-run   SSE 批量连跑：自动挑下一章 → 生成 → 本地 AI 味检测 →
  （不达标时 LLM 去味改写）→ 落库 → 状态文件更新 → 下一章。每次事件为 {event:...} JSON。
- POST /projects/{id}/jobs/{job_id}/review-full   28 维增强审校：确定性 AI 味检测 +
  LLM 结构化审校（逻辑/伏笔/节奏/钩子）+ 追读力提取，总分落 job.review_score。
- GET  /projects/{id}/retention   全书追读力仪表盘数据（由各 job.retention_json 汇总）。

连跑中断策略（daemon 分级思想）：AI 味检测是本地确定性计算（零成本），不达标自动
LLM 改写一次；LLM 生成失败 = 关键问题 → 停止并回报，绝不静默跳章。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..anti_llm import ANTI_LLM_RULES, deflavor_rewrite_prompt, detect
from ..db import get_db
from ..deps import count_words, get_current_user
from ..models import AiChapterJob, AiProject, Chapter, Novel, User, Volume
from ..utils import chapter_display_title, order_chapters, strip_html
from .ai_factory import (  # noqa: F401  router 不复用（避免重复注册），仅借工具函数
    _SYSTEM,
    _chat_text,
    _get_project,
    _hook_alerts,
    _normalize_base,
    _parse_json,
    _pick_config,
    _text_to_html,
    _update_state_files,
)
from .ai_factory import router as _ai_factory_router  # noqa: F401  仅确保初始化顺序

router = APIRouter(prefix="/api/ai-factory", tags=["ai-factory"])




def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/projects/{project_id}/jobs/{job_id}/review-full")
async def review_full(
    project_id: int,
    job_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """28 维增强审校：本地 AI 味检测 + LLM 结构化审校 + 追读力提取，总分落库。"""
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

    # 1. 本地确定性 AI 味检测（零成本）
    deai = detect(text)

    # 2. LLM 结构化审校
    novel = await db.get(Novel, p.novel_id)
    assert novel is not None
    from ..models import Character

    chars = (
        (await db.execute(select(Character).where(Character.novel_id == p.novel_id).limit(10)))
        .scalars()
        .all()
    )
    char_line = "；".join(f"{c.name}（{c.role}）：{(c.description or '')[:100]}" for c in chars)
    spec = json.loads(p.book_spec_json or "{}")
    word_note = ""
    if p.target_chapter_words:
        dev = abs(chapter.word_count - p.target_chapter_words) / p.target_chapter_words
        if dev > 0.5:
            word_note = f"（注意：本章 {chapter.word_count} 字，偏离目标 {p.target_chapter_words} 字超 50%）"

    prompt = (
        f"作品：《{novel.title}》\n立项：{json.dumps(spec, ensure_ascii=False)[:600]}\n"
        f"角色卡：{char_line}\n"
        + (f"前情摘要：{p.global_summary[:800]}\n" if p.global_summary else "")
        + (f"伏笔台账：{p.plot_arcs[:600]}\n" if p.plot_arcs else "")
        + (f"作者意图：{p.author_intent[:300]}\n" if p.author_intent else "")
        + f"\n【待审校章节《{chapter.title or ''}》】{word_note}\n{text[:6000]}\n\n"
        "另外给出本地 AI 味检测结果供参考：\n"
        + json.dumps(deai, ensure_ascii=False)[:600]
        + "\n\n按系统要求输出 JSON。"
    )
    config = await _pick_config(user, db, p.review_llm)
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=2500)
    data = _parse_json(raw)
    if not isinstance(data, dict):
        data = {"score": None, "issues": [], "retention": None}

    issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    issues = issues + [
        {"type": "AI味", "severity": i.get("severity", "low"), "issue": i.get("detail", ""), "suggestion": ""}
        for i in deai["issues"]
        if i.get("severity") in ("high", "medium")
    ]
    # 文字规范检测合并（错别字/敏感词/重复词/标点）
    from ..textlint import lint

    lint_report = lint(text)
    issues = issues + [
        {"type": f"规范·{i['type']}", "severity": i["severity"], "issue": i["detail"], "suggestion": ""}
        for i in lint_report["issues"]
        if i["severity"] in ("high", "medium")
    ]
    retention = data.get("retention") if isinstance(data.get("retention"), dict) else None
    score = data.get("score")
    if isinstance(score, (int, float)) and deai["score"] < 70:
        score = max(0, round(min(score, deai["score"] + 10)))  # AI 味硬上限压分

    job.review_issues = json.dumps(issues, ensure_ascii=False)
    job.review_score = int(score) if isinstance(score, (int, float)) else None
    job.retention_json = json.dumps(retention, ensure_ascii=False) if retention else ""
    has_high = any(i.get("severity") == "high" for i in issues if isinstance(i, dict))
    if has_high and job.status == "done":
        job.status = "needs_fix"
    await db.commit()
    return {
        "score": job.review_score,
        "issues": issues,
        "has_high": has_high,
        "deai_score": deai["score"],
        "lint_score": lint_report["score"],
        "retention": retention,
    }


class HTTPException(Exception):
    """占位防呆：本模块不应直接 raise HTTPException 的场景兜底（实际不会被触发）。"""

    def __init__(self, status: int, msg: str):
        self.status = status
        self.msg = msg


@router.post("/projects/{project_id}/batch-run")
async def batch_run(
    project_id: int,
    count: int = 5,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE 批量连跑（daemon 模式）：自动挑下一章 → 生成 → AI 味检测/改写 → 定稿 → 下一章。

    事件流：{event: "start"|"chapter_start"|"content"|"deai"|"rewrite"|"finalize"|
             "chapter_done"|"error"|"done", ...}
    全部生成完或出错即结束。前端用 AbortController 可随时停。
    """
    p = await _get_project(project_id, user, db)
    if p.status != "writing" or p.novel_id is None:
        raise HTTPException(400, "项目未就绪（需已完成大纲）")

    novel = await db.get(Novel, p.novel_id)
    assert novel is not None
    jobs = (
        (await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id)))
        .scalars()
        .all()
    )
    chapters = (
        (await db.execute(select(Chapter).where(Chapter.novel_id == p.novel_id))).scalars().all()
    )
    volumes = (
        (await db.execute(select(Volume).where(Volume.novel_id == p.novel_id))).scalars().all()
    )
    ordered = order_chapters(chapters, volumes)
    number_map = {c.id: i + 1 for i, c in enumerate(ordered)}
    pending = [j for j in jobs if j.status in ("pending", "failed") and j.chapter_id]
    pending.sort(key=lambda j: number_map.get(j.chapter_id or 0, 99999))
    pending = pending[: max(1, min(count, 10))]

    chapter_llm = await _pick_config(user, db, p.chapter_llm)
    summary_llm = await _pick_config(user, db, p.summary_llm)

    # 上下文组装（复用 M2 的 _assemble_context）
    from .ai_factory import _assemble_context

    async def generate():
        total = len(pending)
        yield _sse({"event": "start", "total": total})
        done_n = 0
        low_quality_streak = 0  # 质量门禁：连续低分（AI味<60）达到 2 章自动暂停
        for job in pending:
            chapter = await db.get(Chapter, job.chapter_id)
            if chapter is None:
                continue
            num = number_map.get(chapter.id, 0)
            title = chapter_display_title(chapter.title, num)
            yield _sse({"event": "chapter_start", "job_id": job.id, "title": title, "index": done_n + 1, "total": total})
            job.status = "writing"
            job.attempt += 1
            await db.commit()

            context = await _assemble_context(p, novel, chapter, job, db)
            system = (
                _SYSTEM
                + "你正在执行整章正文写作任务。中文网文风格，段落短小，对话生动，章末留钩子。"
                + ANTI_LLM_RULES  # 源头控制：写前注入去 AI 味规则
            )
            if p.author_intent:
                system += f"\n【作者长期意图】{p.author_intent[:300]}"
            if p.current_focus:
                system += f"\n【当前阶段焦点】{p.current_focus[:300]}"

            # ---- 生成（复用 _stream_openai 的 httpx 流式）----
            parts: list[str] = []
            url = _normalize_base(chapter_llm.base_url) + "/v1/chat/completions"
            import httpx as _httpx

            try:
                async with _httpx.AsyncClient(timeout=_httpx.Timeout(300.0, connect=15.0)) as client:
                    async with client.stream(
                        "POST",
                        url,
                        json={"model": chapter_llm.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": context}], "stream": True},
                        headers={"Authorization": f"Bearer {chapter_llm.api_key}"},
                    ) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="ignore")[:200]
                            yield _sse({"event": "error", "message": f"AI 接口返回 {resp.status_code}: {body}", "job_id": job.id, "title": title})
                            job.status = "failed"
                            await db.commit()
                            return
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk["choices"][0].get("delta", {}).get("content")
                                if delta:
                                    parts.append(delta)
                                    yield _sse({"event": "content", "job_id": job.id, "text": delta})
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
            except _httpx.HTTPError as exc:
                yield _sse({"event": "error", "message": f"无法连接 AI 接口: {exc.__class__.__name__}", "job_id": job.id, "title": title})
                job.status = "failed"
                await db.commit()
                return

            text = "".join(parts).strip()
            if len(text) < 100:
                yield _sse({"event": "error", "message": "生成内容过短，判定失败", "job_id": job.id, "title": title})
                job.status = "failed"
                await db.commit()
                return

            # ---- AI 味检测 + 自动改写（batch-ai-deflavor 循环思想）----
            report = detect(text)
            yield _sse({"event": "deai", "job_id": job.id, "score": report["score"]})
            if report["score"] < 70:
                yield _sse({"event": "rewrite", "job_id": job.id, "score": report["score"]})
                try:
                    rewritten = await _chat_text(summary_llm, _SYSTEM, deflavor_rewrite_prompt(text, report), max_tokens=6000)
                    rewritten = rewritten.strip()
                    if len(rewritten) > 100:
                        after = detect(rewritten)
                        if after["score"] > report["score"]:
                            text = rewritten
                            yield _sse({"event": "deai", "job_id": job.id, "score": after["score"], "rewritten": True})
                except Exception:  # noqa: BLE001  改写失败就用原稿
                    yield _sse({"event": "deai", "job_id": job.id, "score": report["score"], "rewritten": False})

            # ---- 落库（同 finalize）----
            chapter.content = _text_to_html(text)
            chapter.word_count = count_words(chapter.content)
            if chapter.status == "draft":
                chapter.status = "writing"
            await db.commit()
            try:
                from ..search_fts import sync_chapter

                await sync_chapter(db, chapter.id)
            except Exception:  # noqa: BLE001
                pass

            # ---- 状态文件增量更新（共享函数，失败不阻塞；含伏笔章龄戳记）----
            state_updated = await _update_state_files(p, chapter, text, summary_llm, db, include_particle=True)
            # ---- 人物关系自动同步（失败不阻塞）----
            from .ai_factory import _sync_relations_from_chapter

            await _sync_relations_from_chapter(p, novel, text, summary_llm, db)

            # ---- 本章摘要 + job 收尾 ----
            try:
                job.summary = (
                    await _chat_text(summary_llm, _SYSTEM, f"把以下章节正文压缩成 150 字剧情摘要（只输出摘要）：\n{text[:4000]}", max_tokens=400)
                ).strip()[:500]
            except Exception:  # noqa: BLE001
                pass
            from datetime import datetime, timezone

            job.status = "done"
            job.actual_words = chapter.word_count
            job.finished_at = datetime.now(timezone.utc)
            await db.commit()
            done_n += 1

            # ---- 质量门禁：最终成稿 AI 味 <60 记一记，连续 2 章自动暂停 ----
            final_score = detect(strip_html(chapter.content))["score"]
            if final_score < 60:
                low_quality_streak += 1
                yield _sse({"event": "quality_warn", "job_id": job.id, "title": title, "score": final_score, "streak": low_quality_streak})
            else:
                low_quality_streak = 0

            yield _sse({
                "event": "chapter_done",
                "job_id": job.id,
                "title": title,
                "words": chapter.word_count,
                "state_updated": state_updated,
                "done": done_n,
                "total": total,
            })

            if low_quality_streak >= 2:
                yield _sse({
                    "event": "paused",
                    "reason": f"连续 {low_quality_streak} 章 AI 味低于 60 分，已自动暂停——建议调整提示词或更换正文模型后重跑",
                    "done": done_n,
                    "total": total,
                })
                return

        yield _sse({"event": "done", "completed": done_n, "total": total})

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/projects/{project_id}/retention")
async def retention_dashboard(
    project_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """追读力仪表盘（retention 模块思想的落地）：汇总各章 hooks/cool_points。"""
    p = await _get_project(project_id, user, db)
    jobs = (
        (await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id)))
        .scalars()
        .all()
    )
    hooks: list[dict] = []
    cool_points: list[dict] = []
    chapters_done = 0
    for j in jobs:
        if not j.retention_json:
            continue
        if j.status in ("done", "needs_fix"):
            chapters_done += 1
        try:
            r = json.loads(j.retention_json)
        except (ValueError, TypeError):
            continue
        for h in r.get("hooks", []):
            if isinstance(h, dict) and h.get("desc"):
                hooks.append({"job_id": j.id, **h})
        for c in r.get("cool_points", []):
            if isinstance(c, dict) and c.get("desc"):
                cool_points.append({"job_id": j.id, **c})

    chapters_total = len([j for j in jobs if j.status in ("done", "needs_fix")])
    # 追读力分（webnovel-retention 公式简化版）
    hook_score = min(100, len(hooks) * 12)
    cool_score = min(100, len(cool_points) * 18)
    retention_score = round(hook_score * 0.5 + cool_score * 0.5) if chapters_total else 0
    return {
        "chapters_done": chapters_total,
        "hooks": hooks,
        "cool_points": cool_points,
        "hook_score": hook_score,
        "cool_score": cool_score,
        "retention_score": retention_score,
    }


# ================= M6：修订闭环 + 局部重写 =================

@router.post("/projects/{project_id}/jobs/{job_id}/revise")
async def revise_chapter(project_id: int, job_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """一键按审校意见修订（婉儿包 revise loop 的北斗化）：全文修订保剧情保字数。

    前置：job 已有 review_issues（先跑 review-full）。
    后置：正文落库 + FTS 同步 + 复检 AI 味（review_score 更新）+ 清问题置 done。
    """
    p = await _get_project(project_id, user, db)
    job = await db.get(AiChapterJob, job_id)
    if job is None or job.project_id != p.id or job.chapter_id is None:
        raise HTTPException(404, "章节任务不存在")
    chapter = await db.get(Chapter, job.chapter_id)
    if chapter is None:
        raise HTTPException(404, "章节不存在")
    issues = json.loads(job.review_issues) if job.review_issues else []
    if not issues:
        raise HTTPException(400, "没有待修订的问题（请先运行审校）")

    text = strip_html(chapter.content)
    issue_lines = "\n".join(
        f"- [{i.get('severity', 'medium')}] {i.get('type', '')}：{i.get('issue', '')}（建议：{i.get('suggestion', '')}）"
        for i in issues[:20]
    )
    config = await _pick_config(user, db, p.chapter_llm)
    prompt = (
        f"以下是小说《{p.seed_prompt[:30]}》的一章正文，审校发现以下问题：\n{issue_lines}\n\n"
        "请修订全文。要求：\n"
        "1. 只修复上述问题，不改变剧情走向、人物设定和关键场景\n"
        "2. 保持原篇幅（±15%）\n"
        "3. 遵守以下反 AI 味写作规则：\n" + ANTI_LLM_RULES + "\n"
        "4. 只输出修订后的正文全文，不要任何解释\n\n"
        f"原文：\n{text[:12000]}"
    )
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=8000)
    revised = raw.strip()
    if len(revised) < len(text) * 0.3:
        raise HTTPException(400, "AI 修订结果过短（疑似截断），未保存，请重试")

    chapter.content = _text_to_html(revised)
    chapter.word_count = count_words(chapter.content)
    # 复检 AI 味
    report = detect(revised)
    job.review_score = report["score"]
    job.review_issues = None  # 已按意见修订，清空待办
    job.status = "done"
    job.actual_words = chapter.word_count
    await db.commit()
    try:
        from ..search_fts import sync_chapter

        await sync_chapter(db, chapter.id)
        await db.commit()
    except Exception:  # noqa: BLE001
        pass
    return {
        "word_count": chapter.word_count,
        "deai_score": report["score"],
        "fixed_issues": len(issues),
    }


class RewritePartialIn(BaseModel):
    excerpt: str = Field(min_length=10, max_length=3000)  # 要重写的原文摘段
    instruction: str = Field(default="", max_length=500)


@router.post("/projects/{project_id}/jobs/{job_id}/rewrite-partial")
async def rewrite_partial(
    project_id: int,
    job_id: int,
    data: RewritePartialIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """局部重写：只改指定的摘段，其余原样保留。"""
    p = await _get_project(project_id, user, db)
    job = await db.get(AiChapterJob, job_id)
    if job is None or job.project_id != p.id or job.chapter_id is None:
        raise HTTPException(404, "章节任务不存在")
    chapter = await db.get(Chapter, job.chapter_id)
    if chapter is None:
        raise HTTPException(404, "章节不存在")

    text = strip_html(chapter.content)
    excerpt = data.excerpt.strip()
    pos = text.find(excerpt)
    if pos < 0:
        raise HTTPException(400, "在正文中找不到该摘段（请从正文原样复制，含标点）")

    context_before = text[max(0, pos - 200) : pos]
    context_after = text[pos + len(excerpt) : pos + len(excerpt) + 200]
    config = await _pick_config(user, db, p.chapter_llm)
    prompt = (
        "以下是小说某章的一个段落，请按要求重写它。\n"
        f"前文（供衔接，不要输出）：…{context_before}\n"
        f"【待重写段落】\n{excerpt}\n"
        f"后文（供衔接，不要输出）：{context_after}…\n\n"
        f"重写要求：{data.instruction or '提升表达质量，去除 AI 腔，增强画面感'}\n"
        "只输出重写后的段落文本（长度可与原文不同，但须与前后文自然衔接）。"
    )
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=3000)
    new_excerpt = raw.strip()
    if not new_excerpt:
        raise HTTPException(400, "AI 返回为空，请重试")

    new_text = text[:pos] + new_excerpt + text[pos + len(excerpt) :]
    chapter.content = _text_to_html(new_text)
    chapter.word_count = count_words(chapter.content)
    job.actual_words = chapter.word_count
    await db.commit()
    try:
        from ..search_fts import sync_chapter

        await sync_chapter(db, chapter.id)
        await db.commit()
    except Exception:  # noqa: BLE001
        pass
    return {
        "word_count": chapter.word_count,
        "new_excerpt": new_excerpt,
        "deai_score": detect(new_excerpt)["score"],
    }


# ================= 伏笔到期提醒 + 文本规范检测 =================

@router.get("/projects/{project_id}/hook-alerts")
async def hook_alerts(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """超期伏笔列表（前端进度卡展示：黄灯 ≥8 章，红灯 ≥15 章）。"""
    from .ai_factory import _count_done_jobs

    p = await _get_project(project_id, user, db)
    done_no = await _count_done_jobs(db, p.id)
    alerts = _hook_alerts(p.plot_arcs, done_no)
    return {
        "done_chapters": done_no,
        "alerts": alerts,
        "aging_count": sum(1 for a in alerts if a["level"] == "aging"),
        "overdue_count": sum(1 for a in alerts if a["level"] == "overdue"),
    }


class LintIn(BaseModel):
    text: str = Field(min_length=1, max_length=30000)


@router.post("/projects/{project_id}/lint")
async def lint_text(
    project_id: int, data: LintIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """文本规范检测（本地零成本）：重复词/敏感词/标点问题。

    供编辑器与定稿前自检使用，与 anti_llm 互补（那个查 AI 味，这个查文字规范）。
    """
    await _get_project(project_id, user, db)
    from ..textlint import lint

    return lint(data.text)
