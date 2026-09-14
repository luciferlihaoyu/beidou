"""夜间定时连跑（M13）：后台无人值守的批量生成。

与 batch_run（SSE 交互式）的区别：非流式、无事件推送，结果写入项目
nightly_last_run（JSON），次日用户打开项目页即可看到战报。
复用 ai_factory 的上下文组装/状态文件更新/关系同步全套管线，
质量门禁（连续 2 章 AI 味 <60 即停）同样生效。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from .anti_llm import ANTI_LLM_RULES, detect, deflavor_rewrite_prompt
from .db import SessionLocal
from .models import AiChapterJob, AiProject, Chapter, Novel, Volume
from .routers.ai_factory import (
    _SYSTEM,
    _assemble_context,
    _chat_text,
    _estimate_tokens,
    _normalize_base,
    _pick_config,
    _record_usage,
    _stamp_plot_arcs,  # noqa: F401  保持与 finalize 一致的导入面
    _sync_relations_from_chapter,
    _update_state_files,
)
from .deps import count_words
from .models import User
from .utils import chapter_display_title, order_chapters, strip_html

import httpx

# 夜间执行窗口（服务器本地时区，UTC+8 部署下即凌晨）
NIGHTLY_WINDOW_HOURS = (2, 3, 4)


async def _generate_one(p: AiProject, novel: Novel, job: AiChapterJob, chapter: Chapter, num: int, db) -> dict:
    """后台生成单章：生成→AI味检测/改写→定稿→状态文件→关系同步。返回战报。"""
    from .routers.ai_factory import _text_to_html  # 延迟导入避免循环

    user = await db.get(User, p.user_id)
    chapter_llm = await _pick_config(user, db, p.chapter_llm)
    summary_llm = await _pick_config(user, db, p.summary_llm)

    job.status = "writing"
    job.attempt += 1
    await db.commit()

    context = await _assemble_context(p, novel, chapter, job, db)
    system = (
        _SYSTEM
        + "你正在执行整章正文写作任务。中文网文风格，段落短小，对话生动，章末留钩子。"
        + ANTI_LLM_RULES
    )
    if p.author_intent:
        system += f"\n【作者长期意图】{p.author_intent[:300]}"
    if p.current_focus:
        system += f"\n【当前阶段焦点】{p.current_focus[:300]}"

    # 非流式生成（夜里没人看流，单次请求更简单可靠）
    text = await _chat_text(chapter_llm, system, context, max_tokens=8000)
    text = text.strip()
    if len(text) < 100:
        job.status = "failed"
        await db.commit()
        return {"title": chapter_display_title(chapter.title, num), "ok": False, "error": "生成内容过短"}

    # AI 味检测 + 自动改写（与 batch_run 同阈值）
    report = detect(text)
    rewritten = False
    if report["score"] < 70:
        try:
            new_text = (await _chat_text(summary_llm, _SYSTEM, deflavor_rewrite_prompt(text, report), max_tokens=8000)).strip()
            if len(new_text) >= len(text) * 0.5:
                text = new_text
                rewritten = True
        except Exception:  # noqa: BLE001  改写失败用原文
            pass

    chapter.content = _text_to_html(text)
    chapter.word_count = count_words(chapter.content)
    job.status = "done"
    job.actual_words = chapter.word_count
    job.finished_at = datetime.now(timezone.utc)

    # 定稿摘要（job.summary，供记忆卡）
    try:
        job.summary = (
            await _chat_text(summary_llm, _SYSTEM, f"把以下章节正文压缩成 150 字剧情摘要（只输出摘要）：\n{text[:4000]}", max_tokens=400)
        ).strip()[:500]
    except Exception:  # noqa: BLE001
        pass
    await db.commit()

    # 状态文件 + 伏笔戳记 + 关系同步（与 finalize 一致，含资源账本）
    state_ok = await _update_state_files(p, chapter, text, summary_llm, db, include_particle=True)
    await _sync_relations_from_chapter(p, novel, text, summary_llm, db)
    try:
        from .search_fts import sync_chapter

        await sync_chapter(db, chapter.id)
    except Exception:  # noqa: BLE001
        pass

    # 成本账本（粗估）
    est_prompt = _estimate_tokens(context) + _estimate_tokens(text[:4000])
    est_completion = _estimate_tokens(text) + 120
    if rewritten:
        est_prompt += _estimate_tokens(text[:8000])
        est_completion += _estimate_tokens(text)
    _record_usage(p, est_prompt, est_completion)
    await db.commit()

    final_score = detect(strip_html(chapter.content))["score"]
    return {
        "title": chapter_display_title(chapter.title, num),
        "ok": True,
        "words": chapter.word_count,
        "deai_score": final_score,
        "rewritten": rewritten,
        "state_updated": state_ok,
    }


async def run_nightly_for_project(project_id: int, user_id: int) -> dict:
    """对单个项目执行一次夜间连跑。返回战报（也写入 p.nightly_last_run）。"""
    async with SessionLocal() as db:
        p = await db.get(AiProject, project_id)
        if p is None or p.status != "writing" or p.novel_id is None:
            return {"ok": False, "error": "项目未就绪"}
        novel = await db.get(Novel, p.novel_id)
        if novel is None:
            return {"ok": False, "error": "小说不存在"}

        jobs = ((await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id))).scalars().all())
        chapters = ((await db.execute(select(Chapter).where(Chapter.novel_id == p.novel_id))).scalars().all())
        volumes = ((await db.execute(select(Volume).where(Volume.novel_id == p.novel_id))).scalars().all())
        ordered = order_chapters(chapters, volumes)
        number_map = {c.id: i + 1 for i, c in enumerate(ordered)}
        pending = [j for j in jobs if j.status in ("pending", "failed") and j.chapter_id]
        pending.sort(key=lambda j: number_map.get(j.chapter_id or 0, 99999))
        pending = pending[: max(1, min(p.nightly_chapters or 3, 10))]

        results: list[dict] = []
        low_streak = 0
        for job in pending:
            chapter = await db.get(Chapter, job.chapter_id)
            if chapter is None:
                continue
            num = number_map.get(chapter.id, 0)
            try:
                r = await _generate_one(p, novel, job, chapter, num, db)
            except Exception as exc:  # noqa: BLE001  单章失败不拖垮整夜
                job.status = "failed"
                await db.commit()
                r = {"title": chapter_display_title(chapter.title, num), "ok": False, "error": str(exc)[:100]}
            results.append(r)
            if r.get("ok") and r.get("deai_score", 100) < 60:
                low_streak += 1
                if low_streak >= 2:
                    results.append({"ok": False, "error": "质量门禁：连续 2 章 AI 味 <60，提前收工"})
                    break
            elif r.get("ok"):
                low_streak = 0

        done = sum(1 for r in results if r.get("ok"))
        report = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "done": done,
            "total": len(results),
            "chapters": [r for r in results if r.get("ok")],
            "errors": [r for r in results if not r.get("ok")],
        }
        p.nightly_last_run = json.dumps(report, ensure_ascii=False)
        await db.commit()
        return report


async def nightly_tick() -> int:
    """后台循环一跳：夜间窗口内，给开启定时连跑且今日未跑的项目执行。返回执行项目数。"""
    now = datetime.now()  # 服务器本地时区
    if now.hour not in NIGHTLY_WINDOW_HOURS:
        return 0
    today = now.strftime("%Y-%m-%d")
    async with SessionLocal() as db:
        projects = (
            (await db.execute(select(AiProject).where(AiProject.nightly_enabled.is_(True))))
            .scalars()
            .all()
        )
        todo = []
        for p in projects:
            if p.nightly_last_run:
                try:
                    if json.loads(p.nightly_last_run).get("date") == today:
                        continue  # 今日已跑
                except (json.JSONDecodeError, TypeError):
                    pass
            todo.append((p.id, p.user_id))
    n = 0
    for project_id, user_id in todo:
        bg = BG_TASKS.get(project_id)
        if bg and bg.get("running"):
            continue  # 后台手动连跑在执行，夜跑避让（防双写同一章）
        try:
            await run_nightly_for_project(project_id, user_id)
            n += 1
        except Exception:  # noqa: BLE001  单项目失败不影响其他
            pass
    return n


# ---------- 后台批量连跑（用户手动触发，窗口可关）----------

import asyncio

# 在跑的后台任务注册表：project_id -> 状态 dict（内存态，进程重启即清——足够用，
# 因为任务本身的生命周期也只在本进程内）
BG_TASKS: dict[int, dict] = {}


async def run_batch_background(project_id: int, user_id: int, count: int) -> None:
    """后台批量连跑：与夜跑共用 _generate_one 单章管线，进度写 BG_TASKS 供轮询。

    窗口关闭/断网不影响执行；完成/失败/停止后状态保留在注册表供最后查看。
    """
    entry = BG_TASKS[project_id]
    try:
        async with SessionLocal() as db:
            p = await db.get(AiProject, project_id)
            if p is None or p.status != "writing" or p.novel_id is None:
                entry.update(running=False, error="项目未就绪（需已进入写作阶段）")
                return
            novel = await db.get(Novel, p.novel_id)
            if novel is None:
                entry.update(running=False, error="小说不存在")
                return

            jobs = ((await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id))).scalars().all())
            chapters = ((await db.execute(select(Chapter).where(Chapter.novel_id == p.novel_id))).scalars().all())
            volumes = ((await db.execute(select(Volume).where(Volume.novel_id == p.novel_id))).scalars().all())
            ordered = order_chapters(chapters, volumes)
            number_map = {c.id: i + 1 for i, c in enumerate(ordered)}
            pending = [j for j in jobs if j.status in ("pending", "failed") and j.chapter_id]
            pending.sort(key=lambda j: number_map.get(j.chapter_id or 0, 99999))
            pending = pending[: max(1, min(count, 10))]
            entry["total"] = len(pending)

            low_streak = 0
            for job in pending:
                if entry.get("stop_requested"):
                    entry["results"].append({"ok": False, "error": "已手动停止"})
                    break
                chapter = await db.get(Chapter, job.chapter_id)
                if chapter is None:
                    continue
                num = number_map.get(chapter.id, 0)
                entry["current"] = chapter_display_title(chapter.title, num)
                try:
                    r = await _generate_one(p, novel, job, chapter, num, db)
                except Exception as exc:  # noqa: BLE001  单章失败继续下一章
                    job.status = "failed"
                    await db.commit()
                    r = {"title": chapter_display_title(chapter.title, num), "ok": False, "error": str(exc)[:100]}
                entry["results"].append(r)
                if r.get("ok"):
                    entry["done"] += 1
                    if r.get("deai_score", 100) < 60:
                        low_streak += 1
                        if low_streak >= 2:
                            entry["results"].append({"ok": False, "error": "质量门禁：连续 2 章 AI 味 <60，提前收工"})
                            break
                    else:
                        low_streak = 0
                entry["current"] = ""
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:200]
    finally:
        entry["running"] = False
        entry["finished_at"] = datetime.now(timezone.utc).isoformat()


async def start_batch_background(project_id: int, user_id: int, count: int) -> dict:
    """启动后台连跑；同项目互斥（在跑则拒绝）。返回初始状态。"""
    existing = BG_TASKS.get(project_id)
    if existing and existing.get("running"):
        return {"ok": False, "error": "该项目已有后台连跑在执行", "status": existing}
    BG_TASKS[project_id] = {
        "running": True,
        "done": 0,
        "total": 0,
        "current": "",
        "results": [],
        "error": "",
        "stop_requested": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    asyncio.create_task(run_batch_background(project_id, user_id, count))
    return {"ok": True, "status": BG_TASKS[project_id]}


def get_batch_background(project_id: int) -> dict | None:
    return BG_TASKS.get(project_id)


def stop_batch_background(project_id: int) -> bool:
    entry = BG_TASKS.get(project_id)
    if entry and entry.get("running"):
        entry["stop_requested"] = True
        return True
    return False
