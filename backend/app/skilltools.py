"""技能卡附带脚本的服务端执行（白名单）。

技能卡包里的 `scripts/*.py` 是写给「能跑命令行、能读文件」的 agent 的。北斗是
web 应用，模型没有文件系统——所以这些脚本在应用内**默认等于不存在**，卡里
「先跑脚本再分析」的流程会退化成模型凭感觉估计，甚至谎称已运行脚本。

本模块把其中**核心逻辑是纯函数**的脚本在服务端真实执行，把测量结果作为
「工具输出」注入 prompt。真实数据 ≫ 模型猜测：句长分布、对话占比、爽点信号
密度这些东西，测出来的和图出来的完全是两回事。

## 安全边界

- 只执行**本仓库 vendored 的卡包脚本**：路径由下方 WHITELIST 常量写死，
  不接受任何来自请求的路径、模块名或参数。
- 不使用 subprocess、不执行用户提交的代码；只 import 模块并调用其纯函数。
- 脚本抛异常时**吞掉并返回空**（技能卡不能因为一个统计脚本挂掉就整轮失败），
  但会把失败原因写进返回文本，便于排查。
"""

from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

CARD_DIR = Path(__file__).parent / "skillcards"

# slug → (脚本相对卡包的路径, 要调用的纯函数名, 处理粒度)
# 粒度 chapter = 逐章调用后汇总；corpus = 把全部文本当一个语料调用
WHITELIST: dict[str, tuple[str, str, str]] = {
    "style-fingerprint": ("scripts/style_profile.py", "profile", "corpus"),
    "webnovel-pace-analyzer": ("scripts/pace_scan.py", "analyze_chapter", "chapter"),
    "novel-deconstruction": ("scripts/fact_extractor.py", "extract_facts", "chapter"),
}

# 输入上限：工具是本地纯计算，但正文动辄百万字，仍设上限避免单请求吃满 CPU
MAX_CORPUS_CHARS = 300_000
MAX_CHAPTERS = 40
MAX_CHAPTER_CHARS = 20_000


@lru_cache(maxsize=8)
def _load(slug: str) -> Callable[..., Any] | None:
    """按白名单加载脚本模块并取回目标函数（失败返回 None，不抛）。"""
    spec = WHITELIST.get(slug)
    if spec is None:
        return None
    rel, fn_name, _ = spec
    path = CARD_DIR / slug / rel
    if not path.is_file():
        return None
    try:
        mod_spec = importlib.util.spec_from_file_location(f"beidou_cardtool_{slug.replace('-', '_')}", path)
        if mod_spec is None or mod_spec.loader is None:
            return None
        module = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(module)  # 卡包脚本为纯本地逻辑，无网络/无 IO 副作用
        fn = getattr(module, fn_name, None)
        return fn if callable(fn) else None
    except Exception:
        return None


def available(slug: str) -> bool:
    """该卡是否有可在服务端执行的工具。"""
    return _load(slug) is not None


def _fmt_pairs(items: list, limit: int = 15) -> str:
    return "、".join(f"{k}×{v}" for k, v in items[:limit]) if items else "（无）"


def _fmt_profile(p: dict) -> list[str]:
    """把 style_profile.profile() 的结果排成人能读的几行。"""
    dist = p.get("sentence_len_dist") or {}
    lines = [
        f"- 总字数：{p.get('total_chars', 0):,}；句数：{p.get('sentence_count', 0)}；"
        f"平均句长：{p.get('avg_sentence_len', 0)} 字",
        "- 句长分布：" + "、".join(f"{k} 字 {round(v * 100)}%" for k, v in dist.items()),
        f"- 平均段长：{p.get('avg_para_len', 0)} 字；对话占比：{round(p.get('dialogue_ratio', 0) * 100)}%",
    ]
    per_k = p.get("punct_per_1000_chars") or {}
    if per_k:
        lines.append("- 每千字标点：" + "、".join(f"{k}{v}" for k, v in per_k.items()))
    lines.append("- 高频词：" + _fmt_pairs(p.get("top_words") or []))
    lines.append("- 句首字习惯：" + _fmt_pairs(p.get("top_sentence_starters") or []))
    return lines


def _fmt_pace(rows: list[tuple[str, dict]]) -> list[str]:
    lines = ["| 章节 | 字数 | 疑问/感叹收尾 | 钩子信号 | 爽点信号合计 | 对话占比 |", "|---|---|---|---|---|---|"]
    totals = {"hook": 0, "sat": 0}
    for title, d in rows:
        totals["hook"] += 1 if d.get("hook_question_end") else 0
        totals["sat"] += int(d.get("satisfy_total") or 0)
        lines.append(
            f"| {title} | {d.get('chars', 0):,} | {'是' if d.get('hook_question_end') else '否'} | "
            f"{d.get('hook_keywords', 0)} | {d.get('satisfy_total', 0)} | "
            f"{round((d.get('dialogue_ratio') or 0) * 100)}% |"
        )
    lines.append(
        f"\n汇总：{len(rows)} 章中 {totals['hook']} 章以疑问/感叹收尾；"
        f"爽点信号合计 {totals['sat']} 次（信号词计数，非情节判断，需结合正文解读）。"
    )
    return lines


def _fmt_facts(rows: list[tuple[str, dict]]) -> list[str]:
    """把 fact_extractor.extract_facts() 的逐章结果排成人能读的几行。

    纯函数返回 {facts_numeric/facts_temporal/facts_entity/facts_state/topics}
    (见该函数 docstring);逐章给计数行 + 少量命中摘句(可直接引用的实测证据),
    末尾给全书汇总。
    """
    cats = ("numeric", "temporal", "entity", "state")
    lines: list[str] = []
    totals = dict.fromkeys(cats, 0)
    for title, d in rows:
        counts = []
        for cat in cats:
            n = len(d.get(f"facts_{cat}") or [])
            totals[cat] += n
            counts.append(f"{cat}={n}")
        lines.append(f"- {title}：" + "、".join(counts))
        # 抽样:numeric 前 3 + temporal 前 2 条摘句,够当线索又不淹没正文
        for f in (d.get("facts_numeric") or [])[:3] + (d.get("facts_temporal") or [])[:2]:
            lines.append(f"  · [{f.get('detail', '')}] {f.get('text', '')}")
        topics = d.get("topics") or []
        if topics:
            lines.append(
                "  · 主题/人名候选：" + "、".join(
                    f"{t.get('term', '')}×{t.get('count', 0)}" for t in topics[:10]
                )
            )
    lines.append(
        f"\n汇总：{len(rows)} 章实测——numeric={totals['numeric']}、temporal={totals['temporal']}、"
        f"entity={totals['entity']}、state={totals['state']} 条。"
        "（启发式索引，只保证'让事实碰面'，非完备事实清单，需结合正文核对。）"
    )
    return lines


# 粒度 chapter 的呈现按 slug 分派：slug → (输出标题, 格式化函数)。
# 各卡纯函数返回的 dict 形状不同,格式化函数负责排成人能读的行;
# chapter 粒度但未在此登记的卡保守返回空串,不误注入。
CHAPTER_VIEWS: dict[str, tuple[str, Callable[[list[tuple[str, dict]]], list[str]]]] = {
    "webnovel-pace-analyzer": ("节奏扫描", _fmt_pace),
    "novel-deconstruction": ("事实索引", _fmt_facts),
}


def run_for_card(slug: str, chapters: list[tuple[str, str]]) -> str:
    """对给定章节执行该卡的服务端工具，返回可注入 prompt 的文本块（无工具时返回空串）。

    chapters：[(章节标题, 纯文本正文)]，已由调用方从库里取出。
    """
    spec = WHITELIST.get(slug)
    fn = _load(slug)
    if spec is None or fn is None:
        return ""
    _, _, grain = spec

    try:
        if grain == "corpus":
            text = "\n\n".join(t for _, t in chapters)[:MAX_CORPUS_CHARS]
            if not text.strip():
                return ""
            result = fn(text)
            body = _fmt_profile(result)
            head = (
                "【服务端工具输出·文风指纹】以下数据由技能卡自带脚本 `"
                + spec[0]
                + "` 在服务端对本书正文实测得出（非模型估计）："
            )
        else:
            rows: list[tuple[str, dict]] = []
            for title, text in chapters[:MAX_CHAPTERS]:
                body_text = text[:MAX_CHAPTER_CHARS]
                if not body_text.strip():
                    continue
                rows.append((title, fn(body_text)))
            if not rows:
                return ""
            view = CHAPTER_VIEWS.get(slug)
            if view is None:  # chapter 粒度但未配呈现格式 → 保守返回空
                return ""
            label, fmt = view
            body = fmt(rows)
            head = (
                f"【服务端工具输出·{label}】以下数据由技能卡自带脚本 `"
                + spec[0]
                + "` 在服务端逐章实测得出（非模型估计）："
            )
    except Exception as exc:  # 工具失败不能拖垮整轮技能执行
        return f"【服务端工具输出】脚本 {spec[0]} 执行失败（{type(exc).__name__}: {exc}），请改为直接阅读文本分析。"

    return head + "\n" + "\n".join(str(x) for x in body)
