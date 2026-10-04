"""夜间定时连跑（M13）：后台无人值守的批量生成。

与 batch_run（SSE 交互式）的区别：非流式、无事件推送，结果写入项目
nightly_last_run（JSON），次日用户打开项目页即可看到战报。
复用 ai_factory 的上下文组装/状态文件更新/关系同步全套管线，
质量门禁（连续 2 章 AI 味 <60 即停）同样生效。
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

from sqlalchemy import select

from .anti_llm import ANTI_LLM_RULES, detect, deflavor_rewrite_prompt
from .db import SessionLocal
from .models import AiChapterJob, AiProject, Chapter, Novel, Volume, utcnow
from .chapter_split import _maybe_split_chapter, split_long_chapter  # noqa: F401  re-export
from .routers.ai_factory import (
    _SYSTEM,
    _assemble_context,
    _chat_text,
    _ensure_min_words,
    _estimate_tokens,
    _normalize_base,
    _pick_config,
    _record_usage,
    _stamp_plot_arcs,  # noqa: F401  保持与 finalize 一致的导入面
    _sync_relations_from_chapter,
    _update_state_files,
    maybe_auto_continue_outline,
)
from .deps import count_words
from .models import User
from .utils import chapter_display_title, order_chapters, strip_html

import httpx

# 夜间执行窗口：按「作者所在时区」判定，而不是容器时区。
# 容器（Zeabur 等）常跑 UTC，若直接按服务器本地时区，凌晨 2-4 点会
# 落到北京时间的上午 10-12 点——白天偷偷跑，作者一开页面就在耗算力。
# 可用环境变量覆盖：
#   BEIDOU_NIGHTLY_TZ=Asia/Shanghai   （默认，作者本地时区；设 "local" 用容器时区）
#   BEIDOU_NIGHTLY_HOURS=2,3,4        （窗口内的小时，逗号分隔）
import os as _os

def _nightly_hours() -> tuple[int, ...]:
    raw = _os.getenv("BEIDOU_NIGHTLY_HOURS", "2,3,4")
    hours: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 23:
            hours.append(int(part))
    return tuple(hours) or (2, 3, 4)


NIGHTLY_WINDOW_HOURS = _nightly_hours()


def _nightly_now() -> datetime:
    """夜间窗口判定用的当前时间：默认按作者时区（Asia/Shanghai）。"""
    tz_name = _os.getenv("BEIDOU_NIGHTLY_TZ", "Asia/Shanghai").strip()
    if not tz_name or tz_name.lower() == "local":
        return datetime.now()
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name))
    except Exception:  # 时区库缺失或名字非法 → 退回容器本地时间，不阻断夜跑
        return datetime.now()


# ---------- 超长自动分章（FIX-8B：实现挪到 app/chapter_split.py 共享）----------

# 拆分逻辑（split_long_chapter / _maybe_split_chapter）已挪到 app/chapter_split.py，
# 由夜跑、批量、UI finalize 三条路径统一调用；再导出见顶部 import 区
# （batch.py 的 `from ..nightly import _maybe_split_chapter` 与既有测试导入面不断）。


async def _generate_one_with_retry(p: AiProject, novel: Novel, job: AiChapterJob, chapter: Chapter, num: int, db, on_retry=None) -> dict:
    """单章生成 + 失败原地重试一次。

    首次异常先把 job.status 重置回 pending 并 commit（_generate_one 内部可能
    已部分落库），再重新调用 _generate_one；on_retry 回调用于在结果/日志里
    注明「重试中」。二次失败才标 failed，error 加「重试后仍失败：」前缀。
    """
    try:
        return await _generate_one(p, novel, job, chapter, num, db)
    except Exception as first_exc:  # noqa: BLE001  首次失败：重置状态后原地重试一次
        job.status = "pending"
        await db.commit()
        if on_retry is not None:
            res = on_retry(first_exc)
            if inspect.isawaitable(res):
                await res
        try:
            r = await _generate_one(p, novel, job, chapter, num, db)
            r["retried"] = True
            return r
        except Exception as exc:  # noqa: BLE001  二次失败才放弃
            from .failure_kinds import classify_failure

            reason = f"重试后仍失败：{exc.__class__.__name__}: {exc}"[:2000]
            job.status = "failed"
            # 落库失败原因：否则用户事后只看到 status=failed，无从判断该改什么
            job.last_error = reason
            job.last_error_code = classify_failure(reason).code
            await db.commit()
            return {
                "title": chapter_display_title(chapter.title, num),
                "ok": False,
                "error": reason[:120],
                "reason_code": job.last_error_code,
            }


async def _generate_one(p: AiProject, novel: Novel, job: AiChapterJob, chapter: Chapter, num: int, db) -> dict:
    """夜跑/后台连跑的单章入口：**生成期间登记**，再跑真正的流程。

    过去只有逐章 SSE 端点登记在 ACTIVE_JOBS 里，夜跑一条都不登记。而夜跑一章
    最坏是「上游 15 分钟 + 改写/摘要各 15 分钟 + 关系同步」，**设计上就会超过卡死
    阈值**：用户打开面板触发 _sweep_stuck_jobs，正在正常生成的章节就被捞回 pending
    并盖一句「生成中断」，这条流跑完又写 done——两个写入方互踩同一行状态。
    登记做成上下文管理器（ai_factory.track_active_job），三条路径同一套语义。
    """
    from .routers.ai_factory import track_active_job

    async with track_active_job(job.id):
        return await _generate_chapter(p, novel, job, chapter, num, db)


async def _generate_chapter(p: AiProject, novel: Novel, job: AiChapterJob, chapter: Chapter, num: int, db) -> dict:
    """后台生成单章：生成→AI味检测/改写→定稿→状态文件→关系同步。返回战报。"""
    from .routers.ai_factory import _text_to_html  # 延迟导入避免循环

    user = await db.get(User, p.user_id)
    chapter_llm = await _pick_config(user, db, p.chapter_llm)
    summary_llm = await _pick_config(user, db, p.summary_llm)

    job.status = "writing"
    job.started_at = utcnow()
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

    # 整章生成统一走 _chat_text（FIX-6A 后其内部向上游请求流式并累积 delta）：
    # 夜跑虽然没人看流，但边缘（Cloudflare，524=源站 100 秒无响应）只认
    # 「字节是否在动」——整包一次性返回的思考型模型必被 100 秒掐断（job 5/6/7 实证）。
    text = await _chat_text(chapter_llm, system, context, max_tokens=8000)
    text = text.strip()
    if len(text) < 100:
        job.status = "failed"
        await db.commit()
        job.status = "failed"
        job.last_error = "模型返回内容过短（不足 200 字）"
        job.last_error_code = "empty_output"
        await db.commit()
        return {
            "title": chapter_display_title(chapter.title, num),
            "ok": False,
            "error": "生成内容过短",
            "reason_code": "empty_output",
        }

    # FIX-8A 字数下限：章节字数是最低线，只能上浮——生成后不足就续写一轮拼接。
    # 顺序必须是「先补足、再改写、再拆分」：改写会动措辞（补足要拿原始上下文衔接），
    # 拆分要按最终字数决定（先拆后补会把下限算到段上）。_ensure_min_words 顶层已导入。
    # 复评顺手 5：补足轮 token 精确入账（usage_sink 回填，调用方并入 _record_usage）。
    target_words = p.target_chapter_words or 0
    text_before_topup = text
    topup_usage: dict = {}
    if target_words > 0:
        text = await _ensure_min_words(chapter_llm, system, text, target_words, usage_sink=topup_usage)
    topped_up = target_words > 0 and text != text_before_topup

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

    # 超长自动分章（定稿后）：拆出的后续段只写正文+FTS，
    # 摘要/状态文件/关系同步仍对完整章统一跑（用 text 全文）
    split_into = await _maybe_split_chapter(p, novel, job, chapter, text, db)

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

    # 成本账本（粗估）+ 补足轮精确用量（复评顺手 5：续写轮不再漏计）
    est_prompt = _estimate_tokens(context) + _estimate_tokens(text[:4000])
    est_completion = _estimate_tokens(text) + 120
    if rewritten:
        est_prompt += _estimate_tokens(text[:8000])
        est_completion += _estimate_tokens(text)
    _record_usage(p, est_prompt, est_completion)
    if topup_usage:
        _record_usage(p, int(topup_usage.get("prompt", 0)), int(topup_usage.get("completion", 0)))
    await db.commit()

    final_score = detect(strip_html(chapter.content))["score"]
    result = {
        "title": chapter_display_title(chapter.title, num),
        "ok": True,
        "words": chapter.word_count,
        "deai_score": final_score,
        "rewritten": rewritten,
        "state_updated": state_ok,
        "split_into": split_into,
    }
    if split_into > 1:
        result["title"] = f"{result['title']}（过长自动拆 {split_into} 章）"
    return result


async def run_nightly_for_project(project_id: int, user_id: int) -> dict:
    """对单个项目执行一次夜间连跑。返回战报（也写入 p.nightly_last_run）。"""
    async with SessionLocal() as db:
        p = await db.get(AiProject, project_id)
        if p is None or p.status != "writing" or p.novel_id is None:
            return {"ok": False, "error": "项目未就绪"}
        novel = await db.get(Novel, p.novel_id)
        if novel is None:
            return {"ok": False, "error": "小说不存在"}

        # FIX-8D 自动续写钩子（夜跑开头）：pending 任务不足且离总字数目标还远时，
        # 先给大纲续一批章节（失败不阻断当晚已排的生成——结果只进战报）。
        outline_note: dict | None = None
        try:
            outline_note = await maybe_auto_continue_outline(p, db)
        except Exception:  # noqa: BLE001  续写钩子任何异常都不许拦下夜跑
            outline_note = None

        jobs = ((await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id))).scalars().all())
        chapters = ((await db.execute(select(Chapter).where(Chapter.novel_id == p.novel_id))).scalars().all())
        volumes = ((await db.execute(select(Volume).where(Volume.novel_id == p.novel_id))).scalars().all())
        ordered = order_chapters(chapters, volumes)
        number_map = {c.id: i + 1 for i, c in enumerate(ordered)}
        pending = [j for j in jobs if j.status in ("pending", "failed") and j.chapter_id]
        pending.sort(key=lambda j: number_map.get(j.chapter_id or 0, 99999))
        pending = pending[: max(1, min(p.nightly_chapters or 3, 10))]
        # 钩子刚续出新章时，把新 pending 章排进今晚的队列尾部（仍受 nightly_chapters 上限约束）
        if outline_note and outline_note.get("added_chapters"):
            fresh = [
                j
                for j in (await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id))).scalars().all()
                if j.status == "pending" and j.chapter_id and j not in pending
            ]
            fresh.sort(key=lambda j: getattr(j, "id", 0) or 0)
            pending = pending + fresh[: max(0, min(p.nightly_chapters or 3, 10) - len(pending))]

        results: list[dict] = []
        low_streak = 0
        for job in pending:
            chapter = await db.get(Chapter, job.chapter_id)
            if chapter is None:
                continue
            num = number_map.get(chapter.id, 0)

            def _note_retry(exc: Exception, _title=chapter_display_title(chapter.title, num)) -> None:
                # 战报里注明「重试中」（retry 条目不计入 done/errors 汇总）
                results.append({"title": _title, "ok": False, "retry": True, "error": f"首次失败，重试中：{str(exc)[:80]}"})

            r = await _generate_one_with_retry(p, novel, job, chapter, num, db, on_retry=_note_retry)
            results.append(r)
            if r.get("ok") and r.get("deai_score", 100) < 60:
                low_streak += 1
                if low_streak >= 2:
                    results.append({"ok": False, "error": "质量门禁：连续 2 章 AI 味 <60，提前收工"})
                    break
            elif r.get("ok"):
                low_streak = 0

        finals = [r for r in results if not r.get("retry")]
        done = sum(1 for r in finals if r.get("ok"))
        report = {
            # 日期必须与判定侧（nightly_tick 的 _nightly_now，默认作者时区）同一时钟。
            # 历史 bug：此处曾写 UTC 日期而判定侧读作者时区日期——窗口 2-4 点 CST
            # 对应 UTC 前一天 18-20 点，存进去的日期恒为「昨天」，判重永远失效，
            # tick 每 30 分钟一跳会把整晚的 pending 章节全部跑完（节流形同虚设）。
            "date": _nightly_now().strftime("%Y-%m-%d"),
            "ts": _nightly_now().timestamp(),
            "done": done,
            "total": len(finals),
            "chapters": [r for r in finals if r.get("ok")],
            "errors": [r for r in finals if not r.get("ok")],
            "retries": [r for r in results if r.get("retry")],
        }
        # FIX-8D：自动续写结果 / 全书完本标记写进战报（用户次日打开项目页即可看到）
        if outline_note is not None:
            report["outline_continue"] = outline_note
            if outline_note.get("finished_book"):
                report["finished_book"] = True
        p.nightly_last_run = json.dumps(report, ensure_ascii=False)
        await db.commit()
        return report


# 两次夜跑之间的最小间隔：小于它即视为「同一晚已经跑过」（窗口 2-4 点共 3 小时，
# 留足 20 小时意味着「今晚跑过就不会在明晚之前再跑」）。用时间戳判定而非日期字符串，
# 这样既不受时区影响，也能兼容历史上按 UTC 写入日期的旧战报。
NIGHTLY_MIN_INTERVAL_SECONDS = 20 * 3600


def _already_ran_this_night(last_run: str | None, now: datetime, today: str) -> bool:
    """判断本项目在本次夜间窗口内是否已经跑过。

    1) 有 ts（新格式）→ 距上次 < 20 小时即视为已跑
    2) 无 ts（历史数据）→ 日期等于「今天」或「昨天」即视为已跑
       （历史写入用 UTC，窗口内会写成作者时区的昨天，故必须一并认）
    """
    if not last_run:
        return False
    try:
        data = json.loads(last_run)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    ts = data.get("ts")
    if isinstance(ts, (int, float)):
        return (now.timestamp() - float(ts)) < NIGHTLY_MIN_INTERVAL_SECONDS
    date = data.get("date")
    if not isinstance(date, str):
        return False
    if date == today:
        return True
    try:
        return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(date, "%Y-%m-%d")).days <= 1
    except ValueError:
        return False


async def nightly_tick() -> int:
    """后台循环一跳：夜间窗口内，给开启定时连跑且今日未跑的项目执行。返回执行项目数。"""
    now = _nightly_now()  # 作者时区（默认 Asia/Shanghai），可用 BEIDOU_NIGHTLY_TZ 覆盖
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
            if _already_ran_this_night(p.nightly_last_run, now, today):
                continue
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


async def run_batch_background(project_id: int, user_id: int, count: int, job_ids: list[int] | None = None) -> None:
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
            if job_ids:
                # 指定章节模式（单章后台生成）：只跑点名的 job，忽略 count
                wanted = set(job_ids)
                pending = [j for j in pending if j.id in wanted]
            pending.sort(key=lambda j: number_map.get(j.chapter_id or 0, 99999))
            if not job_ids:
                pending = pending[: max(1, min(count, 10))]
            entry["total"] = len(pending)

            low_streak = 0
            for job in pending:
                if entry.get("stop_requested"):
                    entry["results"].append({"ok": False, "error": "已手动停止", "reason_code": "stopped"})
                    break
                chapter = await db.get(Chapter, job.chapter_id)
                if chapter is None:
                    continue
                num = number_map.get(chapter.id, 0)
                title = chapter_display_title(chapter.title, num)
                entry["current"] = title

                def _note_retry(exc: Exception, _title=title) -> None:
                    # 进度里注明「重试中」（轮询可见）
                    entry["current"] = f"{_title}（重试中：{str(exc)[:60]}）"

                r = await _generate_one_with_retry(p, novel, job, chapter, num, db, on_retry=_note_retry)
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

            # FIX-8D 批末自动续写：一批跑完后 pending 不足且未达标 → 补一批大纲。
            # 失败只记录进 results，不影响本批已完成的章节。
            try:
                note = await maybe_auto_continue_outline(p, db)
                if note is not None:
                    entry["outline_continue"] = note
                    if note.get("ok", True):
                        entry["results"].append(
                            {
                                "ok": True,
                                "outline_continue": True,
                                "added_chapters": note.get("added_chapters", 0),
                                "message": note.get("message", "大纲已自动续写"),
                            }
                        )
                    else:
                        # 复评顺手 4：失败也要留痕（否则书会无声停止生长，没人知道钩子死了）
                        entry["results"].append(
                            {"ok": False, "error": f"大纲自动续写失败：{note.get('outline_continue_error', '未知原因')}"}
                        )
            except Exception as exc:  # noqa: BLE001
                entry["results"].append({"ok": False, "error": f"大纲自动续写失败：{str(exc)[:120]}"})
    except Exception as exc:  # noqa: BLE001
        entry["error"] = str(exc)[:200]
    finally:
        entry["running"] = False
        entry["finished_at"] = datetime.now(timezone.utc).isoformat()


async def start_batch_background(project_id: int, user_id: int, count: int, job_ids: list[int] | None = None) -> dict:
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
    asyncio.create_task(run_batch_background(project_id, user_id, count, job_ids))
    return {"ok": True, "status": BG_TASKS[project_id]}


def get_batch_background(project_id: int) -> dict | None:
    return BG_TASKS.get(project_id)


def stop_batch_background(project_id: int) -> bool:
    entry = BG_TASKS.get(project_id)
    if entry and entry.get("running"):
        entry["stop_requested"] = True
        return True
    return False
