"""三路去 AI 味信号（t7-deai-signals）——诚实口径是本模块的灵魂。

三路分工：
1. 规则路（rule）：anti_llm.detect() 的本地确定性检测，永远可算；
2. 困惑度路（perplexity）：能力探测——向端点发一次带 logprobs 的小请求，
   **响应里真有可用的 logprobs 字段**才算参与。当前默认端点（天枢 MiniMax-M3，
   2026-11-13 实测）HTTP 200 但 choices[0] 只有 finish_reason/index/message 三个键，
   故此路 skipped。换模型后自动变 ok，不许写死「不可用」。
3. 审校师路（reviewer）：LLM 审校，产出「AI 特征 / 人味证据」两组，
   每条必须带能在原文中逐字定位的引文；调用或解析失败 → failed。

统一信封：{"signal", "status": "ok"|"skipped"|"failed"|"uncalibrated", "value", "detail"}

诚实纪律（每一条都有测试钉着）：
- 困惑度路**只认响应里的真实 logprobs**；探测不到就 skipped，
  **严禁**用字数、词频、句长之类启发式伪造一个「困惑度样」的分数糊弄上去；
- 文体区间只认真实标定样本（anti_llm.HUMAN_RANGES，当前为空）→ 全部 uncalibrated，
  **严禁**拍脑袋填一组区间数字冒充标定；
- 审校师失败 → failed，**绝不许**把失败洗成「未发现问题」（value=None，不是空清单）；
- 文案口径：一切分数只能叫「实验风险分」，不得表述成生成来源的概率——
  越界词表硬编码在 tests/test_deai_signals.py 里盯着（源码 + 产出一起扫）。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Callable

import httpx

from .anti_llm import (
    DIMENSION_NAMES,
    HUMAN_RANGES,
    UNCALIBRATED_DETAIL,
    detect,
    dimension_values,
)
from .llm_timeouts import openai_timeout

#: 四种状态的人话口径（学 open-skoob 的 SIGNAL_STATUS_TEXT；t8 报告直接引用）。
SIGNAL_STATUS_TEXT: dict[str, str] = {
    "ok": "正常参与打分",
    "skipped": "当前模型/配置不支持，此路未参与打分",
    "failed": "调用失败，结果不可用（不得当作未发现问题）",
    "uncalibrated": "缺少标定样本，区间未建立",
    "degenerate": "哨兵值，未真正检测（不得当作干净证据）",
}

#: 三路固定顺序（融合分参与路径的输出顺序由此决定，与传入字典的键序无关）。
_PATH_ORDER: list[str] = ["rule", "perplexity", "reviewer"]

#: 融合权重（显式声明；**工程默认约定，不是标定结论**）。依据：
#: - rule 0.5：唯一有 306 条基线长期盯防、确定性可复算的一路 → 基准；
#: - reviewer 0.3：有语义级证据，但无误报率标定数据；
#: - perplexity 0.2：当前模型不可用，将来可用也无标定。
#: 调整权重必须改这里，并同步 tests/test_deai_signals.py 里的硬编码期望（50.0/35.4 等）。
FUSION_WEIGHTS: dict[str, float] = {"rule": 0.5, "perplexity": 0.2, "reviewer": 0.3}

#: 固定免责句（t8 报告页首沿用；越界词的负向扫描见测试）。
_FUSION_CAVEAT = "实验风险分不是 AI 生成概率"


def _envelope(signal: str, status: str, value: Any, detail: str) -> dict:
    return {"signal": signal, "status": status, "value": value, "detail": detail}


# ==================================================================
# 第①路 规则（本地确定性，永远可算）
# ==================================================================

def rule_signal(text: str) -> dict:
    """规则路信封。detect() 由 306 条基线盯防，这里只做信封包装。"""
    r = detect(text)
    detail = "本地确定性规则检测（9 维，无模型参与）"
    if any(i.get("type") == "太短" for i in r["issues"]):
        # 🔴 评审 B-2 major：detect() 对过短文本短路返回 score=100（anti_llm.py:96）。
        # 那个 100 是**哨兵**不是测量值；旧写法把它包成 status="ok" 交给融合，
        # risk=100-100=0 → 报告上显示"完美干净"，而事实是"短到没法检测"。
        # 「没检测」渲染成「没问题」是本项目反复出现的形状，这里从状态层掐断。
        return _envelope(
            "rule",
            "degenerate",
            None,
            "文本过短，规则检测提前短路（score=100 是哨兵值、不具区分力），此路未参与打分",
        )
    return _envelope("rule", "ok", r["score"], detail)


# ==================================================================
# 第②路 困惑度（能力探测，不写死结论）
# ==================================================================

RawCompletion = Callable[[Any, dict], Any]


async def _raw_completion(config: Any, payload: dict) -> dict:
    """非流式原始补全：POST {base_url}/v1/chat/completions，返回解析后的 JSON。

    为什么不走 _chat_text：它为了治 CF 524 强制流式、只回拼接文本，
    **拿不到 choices[0].logprobs**。能力探测必须看到完整响应对象。
    非 200 抛 RuntimeError（带状态码与响应头 200 字），由 perplexity_signal 归类为 failed。
    """
    url = config.base_url.rstrip("/") + "/v1/chat/completions"
    headers = {"Authorization": f"Bearer {config.api_key}"}
    # 探活要快速失败（llm_timeouts 模块注释明说这类交互式探活保持短超时），30s 读超时。
    async with httpx.AsyncClient(timeout=openai_timeout(30)) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"AI 接口返回 {resp.status_code}: {resp.text[:200]}")
        return resp.json()


_PPL_MAX_CHARS = 6000  # 送入上限：再长就只打前 6000 字（一次检测不该烧掉整本的钱）
_PPL_MAX_TOKENS = 4000

#: 困惑度**覆盖率门槛**：与原文对齐的那段 span 内 token 字符数 / 送入文本字数 ≥ 此值，
#: 这个分数才算「代表全文」；低于此值一律 status="degenerate"（拒绝打分）。
#: **未标定，保守取值；宁可拒绝打分也不出不可信的数**（0.9 是工程约定，不是统计结论）。
_PPL_MIN_COVERAGE = 0.9

#: 困惑度**对齐段长度上界**：span 字符数 / 送入文本字数 ≤ 此值（u0d-fix）。
#: 超过说明对齐段比原文还长 ⇒ 中间插入了原文之外的内容，junk 的 token 会进平均值。
#: **一律 degenerate，不许把 >1 的值钳到 1.00 放行**——钳掉等于把 junk 静默吸收，绕过还在原地。
#: **未标定，保守取值**（1.05 只是「排版级重述」的余量，不是统计结论）。
_PPL_MAX_COVERAGE = 1.05

#: 对齐段**之后**允许残留的字符上界（模型收尾客套话的余量，u0d-fix）。
#: 超过即 degenerate：「原文复述两遍」「原文 + junk + 再复现尾部锚点」的第一份都是忠实的，
#: 只靠覆盖率上界看不住尾部堆料，必须由这条拒评。
#: **未标定，保守取值**（既有夹具里合法的收尾「希望有帮助！」= 6 字）。
_PPL_MAX_EDGE_CHARS = 20


def _score_payload(config: Any, text: str) -> tuple[dict, int, bool]:
    """困惑度请求体：**探测与计算合为一发**。

    让模型**逐字复述**被检文本（贪心解码），logprobs 就正好是"模型看这段字时
    每个 token 有多意外"——这才是被检文本的困惑度。旧写法发固定探测句「只回复
    两个字：夜风」，拿到的是探针句的 logprobs，与被检文本**毫无关系**
    （天演评审 B-1 critical：2 字与 3000 字文本得到同一个值）。

    返回 (payload, 实际送入字数, 是否截断)。
    """
    sent = text[:_PPL_MAX_CHARS]
    truncated = len(text) > len(sent)
    return (
        {
            "model": config.model,
            "messages": [
                {"role": "system", "content": "逐字复述用户给出的文本。不加任何说明、"
                                              "不翻译、不总结、不补全，输出与输入完全一致。"},
                {"role": "user", "content": sent},
            ],
            "max_tokens": min(_PPL_MAX_TOKENS, max(64, len(sent) * 2)),
            "temperature": 0,
            "stream": False,
            "logprobs": True,
            "top_logprobs": 1,
        },
        len(sent),
        truncated,
    )


def _logprobs_usable(choice: Any) -> bool:
    """choices[0] 上是否真有可用的 token 级 logprobs。实测反例：键只有
    finish_reason/index/message；另外 logprobs=null 或 content 为空同样不可用。"""
    if not isinstance(choice, dict):
        return False
    lp = choice.get("logprobs")
    if not isinstance(lp, dict):
        return False
    content = lp.get("content")
    if not isinstance(content, list) or not content:
        return False
    return all(isinstance(t, dict) and isinstance(t.get("logprob"), (int, float)) for t in content)


def _token_texts(content: list) -> list[str] | None:
    """把 logprobs.content 的 token 字符串抽出来，用于重建复述文本。

    任一条不是 dict、或缺 "token"、或不是字符串 → None（无法对齐，调用方判 degenerate）。
    """
    out: list[str] = []
    for t in content:
        if not isinstance(t, dict):
            return None
        piece = t.get("token")
        if not isinstance(piece, str):
            return None
        out.append(piece)
    return out


def _aligned_span(sent: str, token_texts: list[str]) -> tuple[list[int], int, int] | None:
    """在模型输出里定位「与原文对应」的那段 span（按 token 边界），返回 (保留下标, 字符数)。

    返回 (参与平均的 token 下标, span 字符数, span 之后残留的字符数)；定位失败 → None
    （保守方向=拒绝打分）。

    锚定口径（u0d-fix，审查反证后改）：
    - 头锚点 ``sent[:20]`` 取**第一次**出现（``find``，不是 ``rfind``）；
    - 尾锚点 ``sent[-20:]`` 只在**头锚点之后**找，且取「第一个让覆盖率够门槛」的出现，
      不全文 rfind 取最后一次。旧写法取最后一次，于是「原文 + junk + 再复现一次尾部锚点」
      能把 junk 一并圈进 span，junk 的 token 进了平均值，还被印成覆盖率 3.73。
    - 「头锚点之后**第一个够门槛**」而不是「头锚点之后第一个」：同质文本里首尾锚点字面
      相同（既有夹具 "烟"*100），纯 first-match 会把 span 截在 40 字上（覆盖率 0.40）。
      真实文本尾锚点唯一，两者完全等价；同质文本下这条与旧 rfind 等价（不会把合格段
      误判成不足）。没有任何一次出现够门槛时退回最后一次，好让下游如实报「覆盖率不足」。
    - span 之后的残留字数一并返回：长篇残留（重复复述/尾部堆 junk）同样拒评，
      阈值见 ``_PPL_MAX_EDGE_CHARS``。

    前置客套话（「好的，以下是复述：」）与后置（「希望有帮助！」）都落在 span 之外，
    **不参与平均**——那些不在原文里的 token 困惑度极高，一起平均会把分数抬成假的。
    参与平均的 token 必须**完全落在 span 内**：跨段边界的 token 一律排除（不做半字计入）。
    """
    joined = "".join(token_texts)
    head, tail = sent[:20], sent[-20:]
    if not sent or not head or not tail:
        return None
    start = joined.find(head)          # 头锚点：**第一次**出现（不是 rfind）
    if start < 0:
        return None
    head_end = start + len(head)

    def _occurrences(from_pos: int) -> list[int]:
        out: list[int] = []
        p = joined.find(tail, from_pos)
        while p >= 0:
            out.append(p)
            p = joined.find(tail, p + 1)
        return out

    # 尾锚点：只在**头锚点之后**找；短文本首尾锚点重叠时退一步到 start+1。
    occ = _occurrences(head_end) or _occurrences(start + 1)
    if not occ:
        return None
    end = 0
    for p in occ:                      # 取「最长但不超过上界」的那次出现（上界内的最后一次）
        if (p + len(tail) - start) / len(sent) <= _PPL_MAX_COVERAGE:
            end = p + len(tail)
    if end <= start:
        # 每一次出现都超上界 → 报最后一次，让下游按「对齐段比原文长」如实拒评
        end = occ[-1] + len(tail)
    kept: list[int] = []
    chars = 0
    pos = 0
    for idx, piece in enumerate(token_texts):
        p_start, p_end = pos, pos + len(piece)
        pos = p_end
        # 只统计**完全落在 span 内**的 token：跨段边界的 token 排除（不做半字计入）
        if not piece or p_start < start or p_end > end:
            continue
        kept.append(idx)
        chars += p_end - p_start
    if not kept or chars <= 0:
        return None
    return kept, chars, len(joined) - end


async def perplexity_signal(
    text: str, config: Any, raw_completion: RawCompletion = _raw_completion
) -> dict:
    """第②路：困惑度。先能力探测，探测不到就 skipped——**此路不参与打分**。

    🔴 红线（本任务最重要的一条）：value 只能来自响应 logprobs 的真实计算
    （exp(-平均 logprob)）。严禁在探测失败的分支里用字数、词频、句长等启发式
    编一个数冒充困惑度——那是伪造数据，不是降级。
    """
    if config is None:
        return _envelope("perplexity", "skipped", None, "未提供模型配置，此路未调用")
    try:
        payload, sent_chars, truncated = _score_payload(config, text)
        data = await raw_completion(config, payload)
        choice = (data.get("choices") or [{}])[0]
    except Exception as exc:  # 网络/HTTP/解析失败：如实报 failed
        return _envelope(
            "perplexity",
            "failed",
            None,
            f"能力探测请求失败：{exc.__class__.__name__}: {str(exc)[:150]}",
        )
    if not _logprobs_usable(choice):
        return _envelope(
            "perplexity",
            "skipped",
            None,
            "当前模型未返回 logprobs，此路未参与打分"
            "（能力探测确认 choices[0] 上没有可用的 logprobs 字段；"
            "实测 2026-11-13：天枢 MiniMax-M3 的 choice 键只有 finish_reason/index/message）",
        )
    content = choice["logprobs"]["content"]
    sent = text[:_PPL_MAX_CHARS]
    finish = choice.get("finish_reason")
    # 输入侧截断的既有披露必须保留（两侧截断要能同时说出来）。
    side = (
        f"送入 {sent_chars} 字"
        + ("（输入侧已截断，此分只代表被截断的部分）" if truncated else "（输入侧未截断）")
    )
    # B｜输出侧截断/过滤：logprobs 覆盖不到全文 → 不许出分（旧实现只看输入侧 truncated）。
    if finish != "stop":
        label = (
            "输出侧被 max_tokens 截断（finish_reason=length）"
            if finish == "length"
            else f"输出侧非正常结束（finish_reason={finish!r}）"
        )
        return _envelope(
            "perplexity",
            "degenerate",
            None,
            f"{label}，logprobs 覆盖不到全文；{side}；本次不出分（保守方向：拒绝打分）",
        )
    # A｜覆盖率门槛：只让与原文对应的 span 内 token 参与平均，覆盖率不够就拒绝打分。
    token_texts = _token_texts(content)
    span = _aligned_span(sent, token_texts) if token_texts is not None else None
    if span is None:
        return _envelope(
            "perplexity",
            "degenerate",
            None,
            "无法对齐复述与原文，可能存在前后缀污染"
            f"（token 里没有字符串，或原文首尾锚点在输出里定位不到）；{side}；本次不出分",
        )
    kept, span_chars, trailing = span
    coverage = span_chars / len(sent)
    if coverage < _PPL_MIN_COVERAGE:
        return _envelope(
            "perplexity",
            "degenerate",
            None,
            f"复述覆盖率 {coverage:.2f} 低于门槛 {_PPL_MIN_COVERAGE}"
            f"（span 内 {span_chars} 字 / 原文 {len(sent)} 字），平均 logprob 只代表这一小段，"
            f"拿它当全文证据等于让小子集冒充全文；{side}；本次不出分",
        )
    if coverage > _PPL_MAX_COVERAGE:
        return _envelope(
            "perplexity",
            "degenerate",
            None,
            f"对齐段比原文长（实际 {coverage:.2f}），疑似中间插入了原文之外的内容"
            f"（span 内 {span_chars} 字 / 原文 {len(sent)} 字，上界 {_PPL_MAX_COVERAGE}）；"
            f"junk 的 token 会被算进平均值，那等于让不属于这篇文本的内容冒充它的困惑度；"
            f"{side}；本次不出分",
        )
    if trailing > _PPL_MAX_EDGE_CHARS:
        return _envelope(
            "perplexity",
            "degenerate",
            None,
            f"对齐段之后仍有 {trailing} 字原文之外的内容（上界 {_PPL_MAX_EDGE_CHARS} 字），"
            f"疑似重复复述整段或尾部插入了原文之外的文字；这些 token 进平均会把分数抬成假的，"
            f"而平均值就是这篇文本困惑度的全部依据；{side}；本次不出分",
        )
    lps = [float(content[i]["logprob"]) for i in kept]
    ppl = math.exp(-sum(lps) / len(lps))
    return _envelope(
        "perplexity",
        "ok",
        round(ppl, 2),
        f"困惑度 = exp(-平均 logprob)，取自模型**逐字复述被检文本**的响应 logprobs"
        f"（{len(lps)} 个 token，覆盖率 {coverage:.2f}；" + side
        + "；值来自真实响应，未使用任何字数/句长启发式",
    )


# ==================================================================
# 第③路 审校师（LLM，两组证据 + 逐字引文核验）
# ==================================================================

_REVIEWER_SYSTEM = (
    "你是资深网文审校师。只输出一个 JSON 对象，不要任何解释或围栏以外的文字。"
)

def _reviewer_prompt(text: str) -> str:
    return (
        "审校下面的章节正文，找出两组证据，只输出 JSON：\n"
        '{"ai_features": [{"quote": "原文原句", "why": "为什么像机器写的"}], '
        '"human_evidence": [{"quote": "原文原句", "why": "为什么像人写的"}]}\n'
        "硬性要求：\n"
        "- quote 必须是正文中逐字存在的连续片段（不超过 60 字）；\n"
        "- 两组都可以为空数组；没有证据就不要编造；\n"
        "- 每组最多 8 条。\n"
        f"【正文】\n{text[:8000]}"
    )


def _extract_json(raw: str) -> Any:
    """容错解析：剥 ``` 围栏，再退而求其次取首个 { 到末个 }。失败抛 ValueError。"""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s.strip())
    try:
        return json.loads(s)
    except ValueError:
        pass
    i, j = s.find("{"), s.rfind("}")
    if 0 <= i < j:
        return json.loads(s[i : j + 1])
    raise ValueError("响应里找不到 JSON 对象")


def _verify_quotes(items: Any, text: str) -> list[dict]:
    """逐条核验引文是否能在原文中逐字定位；幻觉引文标 quote_verified=False，不静默丢弃。"""
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        q = str(it.get("quote") or "").strip()
        out.append(
            {
                "quote": q,
                "why": str(it.get("why") or "").strip(),
                "quote_verified": bool(q) and q in text,
            }
        )
    return out


async def reviewer_signal(text: str, chat_fn: Callable[[str, str], Any] | None) -> dict:
    """第③路：审校师。产出 AI 特征 / 人味证据 两组，每条带原文引文。

    🔴 红线：调用失败或解析失败 → status="failed"、value=None——
    绝不许把失败洗成「未发现问题」（那会让下游把故障当成合格）。
    """
    if chat_fn is None:
        return _envelope("reviewer", "skipped", None, "未提供审校模型函数，此路未调用")
    try:
        raw = await chat_fn(_REVIEWER_SYSTEM, _reviewer_prompt(text))
        data = _extract_json(raw if isinstance(raw, str) else str(raw))
        ai = _verify_quotes(data.get("ai_features"), text)
        human = _verify_quotes(data.get("human_evidence"), text)
    except Exception as exc:
        return _envelope(
            "reviewer",
            "failed",
            None,
            f"审校师调用/解析失败：{exc.__class__.__name__}: {str(exc)[:150]}",
        )
    unverified = sum(1 for it in ai + human if not it["quote_verified"])
    detail = f"AI 特征 {len(ai)} 条、人味证据 {len(human)} 条"
    if unverified:
        detail += f"；{unverified} 条引文未能在原文中逐字定位（quote_verified=false）"
    return _envelope("reviewer", "ok", {"ai_features": ai, "human_evidence": human}, detail)


# ==================================================================
# 文体区间（9 维，未标定就老实说未标定）
# ==================================================================

def style_dimensions(text: str) -> dict[str, dict]:
    """9 维文体值 + 人类区间。区间只认 anti_llm.HUMAN_RANGES（真实标定才填）。

    当前仓库没有任何标定样本 → 每格 human_low/human_high/deviation=None、
    status="uncalibrated"。值可以算（dimension_values），区间不可以编。
    """
    values = dimension_values(text)
    out: dict[str, dict] = {}
    for name in DIMENSION_NAMES:
        v = values.get(name)
        rng = HUMAN_RANGES.get(name)
        if rng is None:
            out[name] = {
                "value": v,
                "human_low": None,
                "human_high": None,
                "deviation": None,
                "status": "uncalibrated",
                "detail": UNCALIBRATED_DETAIL,
            }
        else:
            low, high = float(rng["low"]), float(rng["high"])
            if low <= v <= high:
                dev = 0.0
            elif v < low:
                dev = round(v - low, 2)  # 负=低于人类下界
            else:
                dev = round(v - high, 2)  # 正=高于人类上界
            out[name] = {
                "value": v,
                "human_low": low,
                "human_high": high,
                "deviation": dev,
                "status": "ok",
                "detail": f"区间来源：{rng.get('source', '未注明出处')}",
            }
    return out


# ==================================================================
# 融合分数（确定性纯函数）
# ==================================================================

def _risk_of(signal: str, env: dict) -> float | None:
    """各路 → 0~100 风险分。三个映射都是**显式约定**（无标定），注释即契约：

    - rule：detect.score 是「干净度」（100=无命中），风险 = 100 - score；
    - perplexity：约定单调映射（未标定）——困惑度越高 = 文本对模型越意外 =
      通常更像人写 → 风险越低；50 为映射的中点参考值，仅是曲线形状参数，
      不是「人类困惑度」；
    - reviewer：AI 特征条数占比（含引文未定位的条数——幻觉引文按保守方向计入）；
      两组皆空 = 模型明确报告无特征 → 按约定计 0（弱清洁证据）。
    """
    if env.get("status") != "ok" or env.get("value") is None:
        return None
    if signal == "rule":
        try:
            return round(100.0 - float(env["value"]), 2)
        except (TypeError, ValueError):
            # u0d-fix 裁决B：畸形/不可解析的 value 降级为 None（保守：不猜数），
            # 与 reviewer 分支同款——本模块「形态异常绝不 500」的契约不因哪一路而破例。
            return None
    if signal == "perplexity":
        try:
            return round(100.0 / (1.0 + float(env["value"]) / 50.0), 2)
        except (TypeError, ValueError):
            return None
    if signal == "reviewer":
        v = env["value"]
        if not isinstance(v, dict):  # u0c-fix 裁决B：畸形 value 降级为 None（这条没有），与本模块"形态异常绝不 500"契约对齐；不是 0.0——0.0 专留给两组皆空
            return None
        ai = len(v.get("ai_features") or [])
        human = len(v.get("human_evidence") or [])
        if ai + human == 0:
            return 0.0
        return round(100.0 * ai / (ai + human), 2)
    return None


def fuse_signals(rule: dict, perplexity: dict, reviewer: dict) -> dict:
    """确定性融合：同输入两次结果逐字节一致（无时间、无随机、路径顺序固定）。"""
    envs = {"rule": rule, "perplexity": perplexity, "reviewer": reviewer}
    parts: list[str] = []
    risks: list[float] = []
    notes: list[str] = []
    for name in _PATH_ORDER:
        env = envs[name]
        risk = _risk_of(name, env)
        if risk is not None:
            parts.append(name)
            risks.append(risk)
        else:
            notes.append(f"{name}={env.get('status')}")
    wsum = sum(FUSION_WEIGHTS[n] for n in parts)
    if wsum <= 0:
        score = None
        detail = "无任何一路可用，无融合分"
    elif len(parts) == 1:
        score = round(FUSION_WEIGHTS[parts[0]] * risks[0] / wsum, 1)
        detail = f"仅 1 路参与（{parts[0]}）；其余：{'、'.join(notes)} —— 分数只反映这一路"
    else:
        score = round(
            sum(FUSION_WEIGHTS[n] * r for n, r in zip(parts, risks)) / wsum, 1
        )
        detail = f"{len(parts)} 路参与（{'、'.join(parts)}）；未参与：{'、'.join(notes)}"
    return {
        "score": score,
        "participating": parts,
        "weights": {n: FUSION_WEIGHTS[n] for n in parts},
        "detail": detail,
        "caveat": _FUSION_CAVEAT,
    }


# ==================================================================
# 一站式收集
# ==================================================================

async def collect_signals(
    text: str,
    config: Any = None,
    chat_fn: Callable[[str, str], Any] | None = None,
    raw_completion: RawCompletion = _raw_completion,
) -> dict:
    """三路 + 文体 + 融合一次收集。config/chat_fn 缺省 → 对应路如实 skipped。"""
    rule = rule_signal(text)
    ppl = await perplexity_signal(text, config, raw_completion)
    reviewer = await reviewer_signal(text, chat_fn)
    return {
        "rule": rule,
        "perplexity": ppl,
        "reviewer": reviewer,
        "style": style_dimensions(text),
        "fusion": fuse_signals(rule, ppl, reviewer),
    }
