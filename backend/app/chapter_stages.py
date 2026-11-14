# -*- coding: utf-8 -*-
"""章节内角色接力 + 断点续跑（t6-chapter-stages）。

把一章正文从「一次模型调用写完」升级为可选的分段接力：

    off      默认；不进本模块（nightly._generate_chapter 前置分流，原代码一行不动）
    standard 写作 → 审校 → 修订（三个模型段 + 结算收尾）
    deep     规划 → 编排 → 写作 → 审校 → 修订（五个模型段）+ 结算收尾

设计铁律（docs/批次二-章节分段与三路去AI味-计划.md §3）：

1. **每段落盘**：一段产出立刻覆盖写 ``job.stage_json`` 并 commit——中断（进程死/
   上游炸/限流）后重入，已有键的段直接跳过（open-skoob 的 ``if checkpoints[key]: continue``）。
2. **段间只传上一段产出**：第 1 段拿全量 context（_assemble_context 产物），之后每段
   只见「上一环节产出」+ 本段指令——open-skoob 每段重传全量历史的成本缺陷不学。
3. **结算不调模型**：「结算」不是模型段，是把最后一个 LLM 段产出喂给现有
   ``_update_state_files`` 等收尾管线；战报快照存进 stage_json 的「结算」键，
   完成后的重入 0 次模型调用直接返回同一战报。
4. **模型调用只有一个门**：``_stage_chat``。测试替换这一个名字即可注入假模型
   （假门按 prompt 前缀「【段：X】」解析段名，断点续跑证据 = 断言具体调用序列）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .models import User, utcnow
from .nightly import _chat_text, _ensure_min_words
from .routers.ai_factory import (
    _SYSTEM,
    _assemble_context,
    _estimate_tokens,
    _pick_config,
    _record_usage,
    _sync_relations_from_chapter,
    _update_state_files,
)
from .chapter_split import _maybe_split_chapter
from .anti_llm import detect
from .deps import count_words
from .utils import chapter_display_title, strip_html

#: 档位 → 模型段序列。「结算」固定在其后作为本地收尾段，不在此表。
MODE_STAGES: dict[str, tuple[str, ...]] = {
    "standard": ("写作", "审校", "修订"),
    "deep": ("规划", "编排", "写作", "审校", "修订"),
}

#: 结算段名（非模型段）：定稿 + 摘要 + 状态文件 + 关系同步 + 成本账本
SETTLE_STAGE = "结算"

#: 各模型段的指令。**必须以「【段：X】」开头**——断点续跑的调用序列证据
#: （测试假门解析段名）钉死在这个形状上；段间传递的「上一环节产出」也以它区分于正文。
_STAGE_SPECS: dict[str, str] = {
    "规划": (
        "【段：规划】你是剧情规划师。基于给定的写作上下文，输出本章剧情规划："
        "本章目标、核心冲突、出场人物、开场画面与章末钩子。只输出规划要点，不要写正文。"
    ),
    "编排": (
        "【段：编排】你是场景编排师。基于给定的本章剧情规划，把它编排成有序场景序列："
        "每个场景一行，注明地点、出场人物、情节推进作用与场景衔接。只输出场景序列，不要写正文。"
    ),
    "写作": (
        "【段：写作】你是网文写手。基于给定的素材写出本章完整正文："
        "段落短小、对话生动、章末留钩子。只输出正文，不要章节标题、不要任何解释。"
    ),
    "审校": (
        "【段：审校】你是审校编辑。审校给定的正文：揪出 AI 味表达（模板套词、排比滥用、"
        "上帝视角、章末总结体）与情节硬伤，然后直接输出修订后的全文（不要只列意见、不要解释）。"
    ),
    "修订": (
        "【段：修订】你是终稿润色师。对给定的文稿做最后一轮修订：消灭残留的机械表达与"
        "逻辑毛刺，保持情节与人物完全不变。只输出最终定稿正文，不要任何解释。"
    ),
}

#: 单段生成预算上限（与 off 路整章生成同款：FIX-9 的 16000，推理耗尽由 _chat_text 内部翻倍）
_STAGE_MAX_TOKENS = 16000


async def _stage_chat(llm, system: str, prompt: str, *, max_tokens: int, usage_sink: dict) -> str:
    """**分段产出**的模型调用门（测试注入点）。

    🔴 口径修正（评审 A-7 minor，原话「一切模型交互都从这里走」**不实**）：
    结算摘要与 `_ensure_min_words` 之外的补写轮，以及 `_update_state_files` /
    `_sync_relations` 内部，都另有裸 `_chat_text` 调用。那些是 off 路同款的收尾调用，
    合规但不归这道门管——所以测试必须**另外** patch `_chat_text` 才能钉住它们
    （tests 里 SettlementSpies 多装一个替身就是因为这个，不是随手加的）。
    """
    return await _chat_text(llm, system, prompt, max_tokens=max_tokens, usage_sink=usage_sink)


def _load_state(job) -> dict:
    """读 job.stage_json；损坏/非 dict 一律当空（宁可重跑，不可拿脏数据续跑）。"""
    raw = getattr(job, "stage_json", "") or ""
    if not raw:
        return {}
    try:
        state = json.loads(raw)
    except (ValueError, TypeError):
        # 🔴 评审 A-3 minor：损坏的 stage_json 当空处理 = 「宁可重跑」，
        # 但重跑意味着**静默重烧全部段次**（deep 档 5 次真模型调用）。
        # 用户看不见原因就多花五倍钱，所以必须留痕（下方 job.last_error）。
        return {}
    return state if isinstance(state, dict) else {}


def _save_state(job, state: dict) -> None:
    job.stage_json = json.dumps(state, ensure_ascii=False)


async def run_staged_chapter(p, novel, job, chapter, num: int, db) -> dict:
    """分段接力生成单章。签名与 nightly._generate_chapter 完全一致（分流层无感替换）。

    断点续跑语义：
    - ``job.stage_json`` 里已有的段直接跳过（含跨进程：落盘的 JSON 就是断点）；
    - 跳过段的产出从落盘恢复为「上一环节产出」，继续喂给下一未完成段；
    - 「结算」键存在 = 全部完成，0 次模型调用直接返回存档战报。
    """
    mode = (getattr(p, "chapter_pipeline", "") or "off")
    stages = MODE_STAGES.get(mode)
    if stages is None:
        raise ValueError(f"未知章节流水线档位：{mode!r}（支持 {'/'.join(MODE_STAGES)}）")

    user = await db.get(User, p.user_id)
    chapter_llm = await _pick_config(user, db, p.chapter_llm)
    summary_llm = await _pick_config(user, db, p.summary_llm)

    job.status = "writing"
    job.started_at = utcnow()
    job.attempt += 1
    await db.commit()

    _raw_state = job.stage_json or ""
    state = _load_state(job)
    if _raw_state.strip() and not state:
        # 留痕：有内容却读不出任何段 = stage_json 坏了，本轮会从头重跑
        job.last_error = "stage_json 无法解析，已完成段全部重跑（成本会高于续跑）"
        # 全部完成过的 job 重入：返回存档战报，一个模型调用都不发
        return json.loads(state[SETTLE_STAGE]["text"])

    system = _SYSTEM
    if p.author_intent:
        system += f"\n【作者长期意图】{p.author_intent[:300]}"
    if p.current_focus:
        system += f"\n【当前阶段焦点】{p.current_focus[:300]}"

    prev_text: str | None = None
    sinks: list[dict] = []
    target_words = p.target_chapter_words or 0

    try:
        for stage in stages:
            if state.get(stage):
                # 断点续跑：已落盘的段跳过，产出从落盘恢复为上一环节产出
                prev_text = state[stage]["text"]
                continue

            if prev_text is None:
                # 第 1 段（或其上游全部缺失）：唯一拿全量 context 的段
                context = await _assemble_context(p, novel, chapter, job, db)
                spec = _STAGE_SPECS[stage]
                if stage == "写作" and target_words > 0:
                    spec += f"\n本章正文不少于 {target_words} 字（硬下限）。"
                prompt = f"{spec}\n\n{context}"
            else:
                # 段间只传上一段产出：全量历史与原始 context 不重传（成本铁律）
                spec = _STAGE_SPECS[stage]
                if stage == "写作" and target_words > 0:
                    spec += f"\n本章正文不少于 {target_words} 字（硬下限）。"
                prompt = f"{spec}\n\n【上一环节产出】\n{prev_text}"

            sink: dict = {}
            text = (await _stage_chat(chapter_llm, system, prompt, max_tokens=_STAGE_MAX_TOKENS, usage_sink=sink)).strip()
            sinks.append(sink)

            if stage == "写作":
                # 初稿完整性检查（与 off 路同形状的 failed 战报；已落盘段保留供续跑）
                if len(text) < 100:
                    job.status = "failed"
                    job.last_error = "分段生成内容过短（<100 字）"
                    job.last_error_code = "empty_output"
                    _save_state(job, state)
                    await db.commit()
                    return {
                        "title": chapter_display_title(chapter.title, num),
                        "ok": False,
                        "error": "生成内容过短",
                        "reason_code": "empty_output",
                    }
                if target_words > 0:
                    topup: dict = {}
                    text = (await _ensure_min_words(chapter_llm, system, text, target_words, usage_sink=topup)).strip()
                    sinks.append(topup)

            # 一段完成，立刻落盘（中断安全点）
            state[stage] = {"text": text, "at": utcnow().isoformat()}
            prev_text = text
            _save_state(job, state)
            await db.commit()

    except Exception:
        # 🔴 评审 A-1 major：段产出会落盘，但**成本账（sinks）是局部变量**，
        # 异常一抛就随栈销毁，而记账只在结算区做——中断的章于是永久少记
        # 已真花掉的 token（天演实测：1500 只记 1200，漏 300）。
        # 已花的钱必须先入账再往上抛：章可以失败，账不能丢。
        _sp = sum(int(s.get("prompt", 0)) for s in sinks)
        _sc = sum(int(s.get("completion", 0)) for s in sinks)
        if _sp or _sc:
            _record_usage(p, _sp, _sc)
        raise

    # ---------------- 结算（本地收尾段，不调模型）：最后一个 LLM 段产出即最终正文 ----------------
    final_text = state[stages[-1]]["text"]

    from .routers.ai_factory import _text_to_html  # 延迟导入避免循环（与 off 路同款）

    chapter.content = _text_to_html(final_text)
    chapter.word_count = count_words(chapter.content)
    job.status = "done"
    job.actual_words = chapter.word_count
    job.finished_at = datetime.now(timezone.utc)

    # 超长自动拆分（与 off 路同一管线）
    split_into = await _maybe_split_chapter(p, novel, job, chapter, final_text, db)

    # 定稿摘要（job.summary，供记忆卡；与 off 路同一措辞与预算）
    try:
        job.summary = (
            await _chat_text(
                summary_llm,
                _SYSTEM,
                f"把以下章节正文压缩成 150 字剧情摘要（只输出摘要）：\n{final_text[:4000]}",
                max_tokens=400,
            )
        ).strip()[:500]
    except Exception:  # noqa: BLE001
        pass
    await db.commit()

    # 结算产出喂给现有收尾管线：状态文件 + 关系同步 + FTS（分段路径自带审校/修订两段，
    # 不再叠 off 路的全局 deflavor 改写轮——那是重复劳动）
    state_ok = await _update_state_files(p, chapter, final_text, summary_llm, db, include_particle=True)
    await _sync_relations_from_chapter(p, novel, final_text, summary_llm, db)
    try:
        from .search_fts import sync_chapter

        await sync_chapter(db, chapter.id)
    except Exception:  # noqa: BLE001
        pass

    # 成本账本：段级精确 usage 合计优先，全空才按 off 同款粗估兜底
    prompt_total = sum(int(s.get("prompt", 0)) for s in sinks)
    completion_total = sum(int(s.get("completion", 0)) for s in sinks)
    if prompt_total or completion_total:
        _record_usage(p, prompt_total, completion_total)
    else:
        _record_usage(
            p,
            _estimate_tokens(final_text[:4000]) + _estimate_tokens(final_text) + 120,
            _estimate_tokens(final_text),
        )
    await db.commit()

    final_score = detect(strip_html(chapter.content))["score"]
    result = {
        "title": chapter_display_title(chapter.title, num),
        "ok": True,
        "words": chapter.word_count,
        "deai_score": final_score,
        "rewritten": False,
        "state_updated": state_ok,
        "split_into": split_into,
    }
    if split_into > 1:
        result["title"] = f"{result['title']}（过长自动拆 {split_into} 章）"

    # 结算段落盘：战报快照随「结算」键持久化——完成后的重入零调用
    state[SETTLE_STAGE] = {"text": json.dumps(result, ensure_ascii=False), "at": utcnow().isoformat()}
    _save_state(job, state)
    await db.commit()
    return result
