"""t7-deai-signals 三路信号测试（全部假模型函数，不真调外部端点；真实冒烟由碧霄另跑）。

诚实纪律（计划 §7）：
- 期望值全部**硬编码**（手工推演：_AI_TEXT 疲劳词 42 处/190 字 → 密度 221.05，
  detect 60 分；_HUMAN_TEXT detect 78 分——注意「她说」的「说」会命中 说道? 的可选道）；
- 本文件导入 app.deai_signals 与 anti_llm 新增面（DIMENSION_NAMES/HUMAN_RANGES/dimension_values），
  实现缺席时整个文件收集失败——这是结构红；实现落地后，两个关键判据（不许伪造困惑度、
  不许拍脑袋填区间）靠**变异自证**证明能咬（见任务回报的变异清单）；
- 天生结构性判据在此声明：test_dimension_names_are_the_nine / test_status_text_covers_exactly_four
  是常量形状钉（防漂移），不承担"抓伪造"职责；抓伪造由 5/6/10/12/24 号判据承担。
"""
import asyncio
import json
import pytest
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.anti_llm import DIMENSION_NAMES, HUMAN_RANGES, detect, dimension_values  # noqa: E402
from app.deai_signals import (  # noqa: E402
    FUSION_WEIGHTS,
    SIGNAL_STATUS_TEXT,
    collect_signals,
    fuse_signals,
    perplexity_signal,
    reviewer_signal,
    rule_signal,
    style_dimensions,
)

# ---------------------------------------------------------------- 手工推演的固定文本
# 每段 13 字：他非常紧张，然后突然明白。 疲劳词命中 非常/然后/突然 各 1（共 3/段）
_AI_TEXT = "他非常紧张，然后突然明白。" * 14 + "殊不知其中缘由。"
# 190 字、15 句（14×12 字 + 7 字）、疲劳词 42 处 → 密度 221.05；句长 std 1.25；上帝视角 1
# detect：-20（疲劳>8）-12（句长单调）-8（上帝视角×1）= 60

_HUMAN_TEXT = "他把烟按灭在砖墙上。「走吧。」她说。风从巷口灌进来，卷起地上的糖纸。" * 10
# 340 字、40 句 std=4.60<5 → -12；对话 10 句、said_tags=10（说 命中 说道?）ratio 1.0 → -10
# detect = 78

_REVIEW_TEXT = (
    "夜风掠过屋檐，铜铃轻响。他把刀横在膝上，眯眼看着巷口。「谁？」\n"
    "她没有回答，只是把灯芯捻暗了半分。影子在墙上晃了一下。"
)

_FAKE_CONFIG = types.SimpleNamespace(
    base_url="https://tianshu.example.com", api_key="test-key", model="MiniMax-M3"
)

# 2026-11-13 实测形状：HTTP 200 但 choices[0] 只有 finish_reason/index/message，无 logprobs
def _no_lp_response():
    return {
        "id": "chatcmpl-probe",
        "choices": [
            {"finish_reason": "stop", "index": 0,
             "message": {"role": "assistant", "content": "夜风"}},
        ],
    }


def _lp_response(logprobs):
    return {
        "id": "chatcmpl-probe",
        "choices": [
            {"finish_reason": "stop", "index": 0,
             "message": {"role": "assistant", "content": "夜风"},
             "logprobs": {"content": logprobs}},
        ],
    }


async def _raw_no_logprobs(config, payload):
    return _no_lp_response()


async def _raw_with_logprobs(config, payload):
    return _lp_response([{"token": "夜", "logprob": -0.2}, {"token": "风", "logprob": -2.2}])
# mean(-0.2, -2.2) = -1.2 → ppl = exp(1.2) = 3.3201… → round 3.32


async def _raw_boom(config, payload):
    raise ConnectionError("探针连接被拒")


_REVIEWER_JSON = json.dumps(
    {
        "ai_features": [
            {"quote": "夜风掠过屋檐，铜铃轻响", "why": "环境音开场"},
            {"quote": "原文里不存在的一句引文", "why": "幻觉引文"},
        ],
        "human_evidence": [
            {"quote": "把灯芯捻暗了半分", "why": "动作具体"},
        ],
    },
    ensure_ascii=False,
)


async def _chat_reviewer_ok(system, prompt):
    return _REVIEWER_JSON


async def _chat_boom(system, prompt):
    raise RuntimeError("审校端点超时")


async def _chat_garbage(system, prompt):
    return "抱歉，我无法输出结构化结果。"


def _skipped_envelope(signal):
    return {"signal": signal, "status": "skipped", "value": None, "detail": "夹具"}


# ================================================================ 1. 统一信封形状
def test_envelopes_have_exact_unified_shape():
    rule = rule_signal(_AI_TEXT)
    assert set(rule.keys()) == {"signal", "status", "value", "detail"}
    assert rule["signal"] == "rule"
    ppl = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, _raw_no_logprobs))
    assert set(ppl.keys()) == {"signal", "status", "value", "detail"}
    assert ppl["signal"] == "perplexity"
    rev = asyncio.run(reviewer_signal(_REVIEW_TEXT, _chat_reviewer_ok))
    assert set(rev.keys()) == {"signal", "status", "value", "detail"}
    assert rev["signal"] == "reviewer"
    style = style_dimensions(_AI_TEXT)
    for cell in style.values():
        assert set(cell.keys()) == {"value", "human_low", "human_high", "deviation", "status", "detail"}


# ================================================================ 2. 第①路 规则
def test_rule_signal_scores_hardcoded():
    assert rule_signal(_AI_TEXT)["value"] == 60
    assert rule_signal(_HUMAN_TEXT)["value"] == 78
    # 与 detect() 既有输出一致（detect 由 306 条基线另行盯防，此处是 t7 本地钉）
    assert detect(_AI_TEXT)["score"] == 60


def test_rule_signal_short_text_sentinel_noted():
    """🔴 契约变更 2026-11-14（评审 B-2 major）：这条**原先给 bug 背书**。

    原断言是 `status == "ok"` 且 `value == 100`——也就是承认「太短哨兵可以披着
    真实测量的外衣进融合」，只在 detail 里补一句解释。可融合层**不看 detail**，
    risk = 100 - 100 = 0 于是报出"完美干净"。这与 B-1（困惑度那条反向背书）同族：
    **判据把实现里那个错误形状钉成了合约**。
    """
    env = rule_signal("太短了。")
    assert env["status"] == "degenerate", f"哨兵仍以 {env['status']} 出场"
    assert env["value"] is None, "哨兵 100 不是测量值，不许当 value 交给融合"
    assert "过短" in env["detail"] or "太短" in env["detail"]


def test_detect_shape_unchanged():
    r = detect(_AI_TEXT)
    assert set(r.keys()) == {"score", "issues"}
    assert detect(_HUMAN_TEXT)["score"] == 78
    assert detect("短")["score"] == 100


# ================================================================ 3. 第②路 困惑度（能力探测）
def test_perplexity_probe_payload_requests_logprobs():
    captured = {}

    async def fake_raw(config, payload):
        captured["payload"] = payload
        return _no_lp_response()

    asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, fake_raw))
    assert captured["payload"]["logprobs"] is True
    assert captured["payload"]["top_logprobs"] == 1
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["model"] == "MiniMax-M3"
    assert captured["payload"].get("temperature") == 0, "复述必须贪心解码，否则复述不出原文"
    # 🔴 评审 B-1 连带修正：这条原先写的是 `max_tokens <= 16`（"能力探测必须省 token"），
    # 它和「发固定探测句」是**同一个错误合约的两面**——预算压到 16 就只够回两个字，
    # 于是实现只能发探针句。现在钉的是「有预算上限但不许小到装不下文本」。
    assert 16 < captured["payload"]["max_tokens"] <= 4096, captured["payload"]["max_tokens"]


def test_perplexity_skipped_when_model_has_no_logprobs():
    env = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, _raw_no_logprobs))
    assert env["status"] == "skipped"
    assert env["value"] is None
    assert "logprobs" in env["detail"]
    # logprobs 字段在场但不可用（null / 空 content / 无 logprob 数值）同样 skipped
    assert asyncio.run(
        perplexity_signal(_AI_TEXT, _FAKE_CONFIG,
                          lambda c, p: _async_of(_lp_response([])))
    )["status"] == "skipped"
    assert asyncio.run(
        perplexity_signal(_AI_TEXT, _FAKE_CONFIG,
                          lambda c, p: _async_of({"choices": [{"finish_reason": "stop", "index": 0,
                                                               "message": {}, "logprobs": None}]}))
    )["status"] == "skipped"


def _capture_raw(logprobs):
    """假响应同时**抓住请求体**，用于钉「请求里带的是被检文本本身」。"""
    seen = []

    async def raw(config, payload):
        seen.append(payload)
        return _lp_response(logprobs)

    return raw, seen


def test_perplexity_request_carries_the_text_under_inspection():
    """🔴 评审 B-1 critical 的根治判据：**探测与计算合一**。

    原实现把 `text` 只留在签名里，请求体恒为固定探测句「只回复两个字：夜风」，
    于是 2 字文本和 3000 字文本拿到**同一个困惑度**——换支持 logprobs 的模型后，
    这一路会把「探针句的困惑度」冒充「整章的困惑度」参与融合。
    所以这条判据钉的不是数值，是**送出去的内容**：必须是被检文本本身。
    """
    raw, seen = _capture_raw([{"token": "他", "logprob": -0.2}, {"token": "紧", "logprob": -2.2}])
    a = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, raw))
    b = asyncio.run(perplexity_signal(_HUMAN_TEXT, _FAKE_CONFIG, raw))
    assert len(seen) == 2
    body_a = json.dumps(seen[0], ensure_ascii=False)
    body_b = json.dumps(seen[1], ensure_ascii=False)
    assert "他非常紧张" in body_a, "请求体里没有 A 文本的内容——困惑度与文本无关（B-1 形状）"
    assert "糖纸" in body_b, "请求体里没有 B 文本的内容——困惑度与文本无关（B-1 形状）"
    assert "只回复两个字：夜风" not in body_a, "仍在发固定探测句冒充打分"
    assert body_a != body_b, "两份不同文本发出的是同一请求，分数必然张冠李戴"
    assert a["status"] == "ok" and b["status"] == "ok"


def test_perplexity_value_follows_logprobs_not_text_stats():
    """反启发式判据的**正确方向**：同一文本、不同 logprobs → 值必须不同。

    原先这条写反了（两个不同文本 + 同一个假响应 → 值必须一模一样），
    那等于给「text 参数没用」签担保书。真正的启发式伪造长这样：
    logprobs 变了值却不动（说明值是从字数/句长度出来的）。
    """
    raw1, _ = _capture_raw([{"token": "他", "logprob": -0.2}, {"token": "紧", "logprob": -2.2}])
    raw2, _ = _capture_raw([{"token": "他", "logprob": -0.1}, {"token": "紧", "logprob": -0.3}])
    a = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, raw1))
    b = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, raw2))
    assert a["value"] == 3.32  # exp(-(-0.2-2.2)/2)，手算硬编码
    assert b["value"] != a["value"], "logprobs 变了值不动 → 值不是从 logprobs 来的"
    assert "2" in a["detail"]  # detail 交代取了几个 token


def test_perplexity_truncates_long_text_and_says_so():
    """超长正文必须截断（否则一次检测烧掉整本的钱），且**在 detail 里说明截断**。"""
    long_text = "烟" * 30000
    raw, seen = _capture_raw([{"token": "烟", "logprob": -1.0}])
    env = asyncio.run(perplexity_signal(long_text, _FAKE_CONFIG, raw))
    sent = seen[0]["messages"][-1]["content"]
    assert len(sent) < len(long_text), "超长文本没截断"
    assert "截断" in env["detail"], f"截断了却没说明：{env['detail'][:80]}"


def test_perplexity_failed_on_transport_error():
    env = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, _raw_boom))
    assert env["status"] == "failed"
    assert env["value"] is None
    assert "探针连接被拒" in env["detail"]


def test_perplexity_skipped_when_no_config_without_calling():
    calls = []

    async def never(config, payload):
        calls.append(payload)
        raise AssertionError("未提供配置时不许发请求")

    env = asyncio.run(perplexity_signal(_AI_TEXT, None, never))
    assert env["status"] == "skipped"
    assert calls == []


async def _async_of(value):
    return value


# ================================================================ 4. 第③路 审校师
def test_reviewer_ok_with_quote_verification():
    env = asyncio.run(reviewer_signal(_REVIEW_TEXT, _chat_reviewer_ok))
    assert env["status"] == "ok"
    ai = env["value"]["ai_features"]
    human = env["value"]["human_evidence"]
    assert len(ai) == 2 and len(human) == 1
    assert ai[0]["quote_verified"] is True
    assert ai[1]["quote_verified"] is False  # 幻觉引文必须被标出
    assert human[0]["quote_verified"] is True
    assert "1 条引文未能" in env["detail"]


def test_reviewer_failure_is_not_silence():
    """🔴 调用失败绝不许伪装成「没发现问题」：value 必须是 None 而不是空清单。"""
    env = asyncio.run(reviewer_signal(_REVIEW_TEXT, _chat_boom))
    assert env["status"] == "failed"
    assert env["value"] is None
    assert env["value"] != {"ai_features": [], "human_evidence": []}
    assert "审校端点超时" in env["detail"]
    env2 = asyncio.run(reviewer_signal(_REVIEW_TEXT, _chat_garbage))
    assert env2["status"] == "failed"
    assert env2["value"] is None


def test_reviewer_skipped_without_chat_fn():
    env = asyncio.run(reviewer_signal(_REVIEW_TEXT, None))
    assert env["status"] == "skipped"
    assert env["value"] is None


# ================================================================ 5. 文体区间（9 维）
def test_dimension_names_are_the_nine():
    assert DIMENSION_NAMES == [
        "疲劳词密度", "禁用句式", "句长标准差", "对话标签比", "段落均匀度",
        "上帝视角", "章末总结体", "三连排比", "套词密度",
    ]


def test_style_all_nine_uncalibrated():
    """🔴 未标定就老实说未标定：区间不许有任何编造数字。"""
    style = style_dimensions(_AI_TEXT)
    assert list(style.keys()) == DIMENSION_NAMES
    assert len(style) == 9
    for cell in style.values():
        assert cell["status"] == "uncalibrated"
        assert cell["human_low"] is None
        assert cell["human_high"] is None
        assert cell["deviation"] is None
        assert "缺少标定样本" in cell["detail"]
    # 手工推演的维度值（值可以算，区间不可以编）
    assert style["疲劳词密度"]["value"] == 221.05
    assert style["句长标准差"]["value"] == 1.25
    assert style["对话标签比"]["value"] is None
    assert style["段落均匀度"]["value"] is None
    assert style["上帝视角"]["value"] == 1
    assert style["套词密度"]["value"] == 0.0
    # 仓库当前没有任何标定样本 → 区间表必须为空
    assert HUMAN_RANGES == {}


def test_style_calibrated_branch_mechanics_with_fixture_range(monkeypatch):
    """夹具区间只验证机制（deviation/来源），非真实标定——真标定必须走样本统计。"""
    from app import deai_signals
    monkeypatch.setitem(
        HUMAN_RANGES, "疲劳词密度",
        {"low": 0.0, "high": 8.0, "source": "测试夹具，非真实标定"},
    )
    style = deai_signals.style_dimensions(_AI_TEXT)
    cell = style["疲劳词密度"]
    assert cell["status"] == "ok"
    assert cell["human_low"] == 0.0 and cell["human_high"] == 8.0
    assert cell["deviation"] == 213.05  # 221.05 - 8.0
    assert "测试夹具" in cell["detail"]
    # 未命中的维度仍是 uncalibrated
    assert style["句长标准差"]["status"] == "uncalibrated"


def test_dimension_values_refuse_short_text():
    vals = dimension_values("太短了。")
    assert set(vals.keys()) == set(DIMENSION_NAMES)
    assert all(v is None for v in vals.values())


# ================================================================ 6. 融合分数
def test_fusion_single_participant_declares_it_and_pins_weights():
    assert FUSION_WEIGHTS == {"rule": 0.5, "perplexity": 0.2, "reviewer": 0.3}
    rule = rule_signal(_HUMAN_TEXT)  # 78 → 风险 22.0
    f = fuse_signals(rule, _skipped_envelope("perplexity"), _skipped_envelope("reviewer"))
    assert f["score"] == 22.0
    assert f["participating"] == ["rule"]
    assert f["weights"] == {"rule": 0.5}
    assert "仅 1 路参与" in f["detail"]
    assert f["caveat"] == "实验风险分不是 AI 生成概率"


def test_fusion_rule_and_reviewer_weighted():
    """rule 60(风险40,权重0.5) + reviewer 2AI/1人味(风险66.67,权重0.3) → (20+20.001)/0.8 = 50.0"""
    rule = rule_signal(_AI_TEXT)
    rev = asyncio.run(reviewer_signal(_REVIEW_TEXT, _chat_reviewer_ok))
    f = fuse_signals(rule, _skipped_envelope("perplexity"), rev)
    assert f["score"] == 50.0
    assert f["participating"] == ["rule", "reviewer"]  # 固定路径顺序，不随输入次序
    assert "perplexity=skipped" in f["detail"]


def test_fusion_rule_and_perplexity_weighted():
    """rule 78(风险=100-78=22.0,权重0.5) + ppl 3.32(风险93.77,权重0.2)
    → (0.5×22 + 0.2×93.77)/0.7 = 29.754/0.7 = 42.5057 → 42.5"""
    rule = rule_signal(_HUMAN_TEXT)
    ppl = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, _raw_with_logprobs))
    f = fuse_signals(rule, ppl, _skipped_envelope("reviewer"))
    assert ppl["value"] == 3.32
    assert f["score"] == 42.5


def test_fusion_failed_path_never_contributes():
    rule = rule_signal(_HUMAN_TEXT)  # 风险 22.0
    failed = {"signal": "reviewer", "status": "failed", "value": None, "detail": "x"}
    f = fuse_signals(rule, _skipped_envelope("perplexity"), failed)
    assert f["score"] == 22.0
    assert f["participating"] == ["rule"]
    assert "reviewer=failed" in f["detail"]


def test_fusion_byte_stable_across_calls():
    rule = rule_signal(_AI_TEXT)
    rev = asyncio.run(reviewer_signal(_REVIEW_TEXT, _chat_reviewer_ok))
    a = fuse_signals(rule, _skipped_envelope("perplexity"), rev)
    b = fuse_signals(rule, _skipped_envelope("perplexity"), rev)
    da = json.dumps(a, sort_keys=True, ensure_ascii=False)
    db = json.dumps(b, sort_keys=True, ensure_ascii=False)
    assert da == db  # 同输入两次逐字节一致


# ================================================================ 7. 状态口径与越界文案
def test_status_text_covers_exactly_five():
    """degenerate 于 2026-11-14 入合约（评审 B-2），所以这张表是五键不是四键。"""
    assert set(SIGNAL_STATUS_TEXT) == {
        "ok",
        "skipped",
        "failed",
        "uncalibrated",
        "degenerate",
    }
    for v in SIGNAL_STATUS_TEXT.values():
        assert isinstance(v, str) and v


def test_no_overstepping_wording_anywhere():
    """🔴 文案红线：期望串硬编码，扫实现源码 + 全部产出文本。"""
    forbidden = ("AI 概率", "是 AI 生成的可能性")
    app_dir = Path(__file__).resolve().parent.parent / "app"
    blobs = [
        (app_dir / "deai_signals.py").read_text(encoding="utf-8"),
        (app_dir / "anti_llm.py").read_text(encoding="utf-8"),
        json.dumps(SIGNAL_STATUS_TEXT, ensure_ascii=False),
    ]
    out = asyncio.run(collect_signals(_AI_TEXT, config=_FAKE_CONFIG,
                                      chat_fn=_chat_boom, raw_completion=_raw_no_logprobs))
    blobs.append(json.dumps(out, ensure_ascii=False))
    for blob in blobs:
        for w in forbidden:
            assert w not in blob, f"越界表述「{w}」泄漏"
    assert out["fusion"]["caveat"] == "实验风险分不是 AI 生成概率"


# ================================================================ 8. 一站式收集
def test_collect_signals_end_to_end_deterministic_and_honest():
    out1 = asyncio.run(collect_signals(_AI_TEXT, config=_FAKE_CONFIG,
                                       chat_fn=_chat_boom, raw_completion=_raw_no_logprobs))
    out2 = asyncio.run(collect_signals(_AI_TEXT, config=_FAKE_CONFIG,
                                       chat_fn=_chat_boom, raw_completion=_raw_no_logprobs))
    assert json.dumps(out1, sort_keys=True, ensure_ascii=False) == json.dumps(
        out2, sort_keys=True, ensure_ascii=False)
    assert out1["rule"]["status"] == "ok"
    assert out1["perplexity"]["status"] == "skipped"
    assert out1["reviewer"]["status"] == "failed"
    assert "审校端点超时" in out1["reviewer"]["detail"]
    assert out1["fusion"]["participating"] == ["rule"]
    assert out1["fusion"]["score"] == 40.0  # 仅规则路：100 - 60
    assert "仅 1 路参与" in out1["fusion"]["detail"]
    assert len(out1["style"]) == 9


def test_collect_signals_without_model_config_skips_those_paths():
    out = asyncio.run(collect_signals(_AI_TEXT))
    assert out["perplexity"]["status"] == "skipped"
    assert "未提供模型配置" in out["perplexity"]["detail"]
    assert out["reviewer"]["status"] == "skipped"
    assert out["fusion"]["score"] == 40.0
    assert "perplexity=skipped" in out["fusion"]["detail"]
    assert "reviewer=skipped" in out["fusion"]["detail"]


# ------------------------------------------------ 评审 B-2 major：哨兵假干净


def test_short_text_sentinel_is_not_reported_as_a_clean_score():
    """🔴 detect() 对过短文本**短路返回 score=100**（anti_llm.py:96 的「太短」哨兵）。

    旧实现把这个 100 当真实干净度包成 `status="ok"` 交给融合，
    于是 risk = 100-100 = 0 → **"完美干净"**。
    而这句话其实根本没有检测过（文本太短没法检测）——
    「没检测」被渲染成了「没问题」，本项目反复出现的形状。
    所以状态必须是 degenerate，并且**不参与打分**。
    """
    env = rule_signal("太短了。")
    assert env["status"] == "degenerate", (
        f"过短哨兵不该披着 ok 出场（实得 status={env['status']!r}、value={env['value']!r}）"
    )
    assert env["value"] is None, "哨兵 100 不是测量值，不许冒充 value 进入融合"
    assert "太短" in env["detail"] or "过短" in env["detail"]


def test_fusion_refuses_when_the_only_path_is_the_sentinel():
    """只有哨兵时融合分必须是 None，**不许产出 0.0 冒充"很干净"**。"""
    rule = rule_signal("太短了。")
    ppl = _skipped_envelope("perplexity")
    rev = _skipped_envelope("reviewer")
    fused = fuse_signals(rule, ppl, rev)
    assert fused["participating"] == [], f"哨兵混进了参与路：{fused['participating']}"
    assert fused["score"] is None, f"无可用路却产出了分数 {fused['score']}（0.0=完美干净假象）"
    assert fused["detail"], "必须交代为什么没有分"


def test_normal_length_text_still_scores_ok():
    """反向对照：正常长度的干净文本仍应是 ok + 真分数，别把修哨兵修成一律降级。"""
    env = rule_signal("他把烟按灭在砖墙上。「走吧。」她说。风从巷口灌进来，卷起地上的糖纸。" * 4)
    assert env["status"] == "ok", f"正常文本被误降级：{env['status']}"
    assert isinstance(env["value"], (int, float))


# ---- 评审 B3：引文逐字校验的字符级攻击用例（行为已对，补上锁）


@pytest.mark.parametrize(
    "quote,expect_verified",
    [
        ("风从巷口灌进来", True),      # 真子串
        ("风 从 巷 口 灌 进 来", False),  # 多空格：不许规范化后放行
        ("風从巷口灌進來", False),      # 全/简体不同 → 不是逐字
        ("风从巷口灌进", True),        # 删字后仍是真子串：逐字存在的正确行为
        ("风从巷口灌进来。", False),     # 补标点：原文没有句号
    ],
    ids=["exact", "extra-spaces", "traditional-form", "shorter-substring", "added-punct"],
)
def test_quote_verification_is_literal_at_character_level(quote, expect_verified):
    """🔴 评审 B3：`quote_verified` 用**裸子串**，不许任何规范化。

    这组用例锁住边界：字符级变形（空格/繁简/标点）**全部判未命中**（保守方向正确），
    而"删字后的真子串"判命中——那是「逐字存在」的定义本身。
    方法固有边界要写在这里，别让下一个人以为它还能防语义级断章取义：
    **引文逐字存在 ≠ 引文支持那个论断**，后者不在本函数职责内。
    """
    text = "他把烟按灭在砖墙上。风从巷口灌进来，卷起地上的糖纸。" * 3
    payload = json.dumps(
        {"ai_features": [{"quote": quote, "why": "开场套路"}], "human_evidence": []},
        ensure_ascii=False,
    )

    async def chat(system, prompt):
        return payload

    got = asyncio.run(reviewer_signal(text, chat))
    assert got["status"] == "ok"
    feats = got["value"]["ai_features"]
    assert feats[0]["quote_verified"] is expect_verified, (
        f"引文 {quote!r} 应判 verified={expect_verified}，实得 {feats[0]['quote_verified']}"
    )


# ================================================================ u0c 补判据（M / E / S2）
# 背景：独立评审用变异实验实测——把生产逻辑改坏，既有测试仍全绿（"有名字、零牙齿"）。
# 期望值全部硬编码，不从实现 import 常量当基准。


# ---------------- 契约 M：_risk_of 的「空输入 → 0.0」显式契约 ----------------
# 现状：实现**恰好**返回 0.0，但没有任何测试钉住这个形状（把默认值改成 0.5 或让它抛异常，
# 既有 35 条全绿）。以下三种畸形输入各钉一个硬编码期望值。

def test_u0c_risk_of_empty_envelope_is_none_not_zero():
    """完全空的信封（连 status 都没有）→ None（不参与），**不许是 0.0**。
    0.0 = 「已检测且完美干净」；None = 「此路无话可说」。两者混同是把故障洗成合格。"""
    from app.deai_signals import _risk_of

    assert _risk_of("reviewer", {}) is None
    assert _risk_of("reviewer", {"status": "ok", "value": None}) is None
    assert _risk_of("mystery_path", {"status": "ok", "value": 1.0}) is None, \
        "未登记信号名必须返回 None，不许猜一个分"


def test_u0c_risk_of_reviewer_both_groups_empty_is_exactly_zero():
    """审校师返回空（两组皆空数组 / value={} / 两组键缺失 / 值为 None）→ **恰好 0.0**。
    这是显式约定「模型明确报告无特征 = 弱清洁证据」，不是巧合。"""
    from app.deai_signals import _risk_of

    cases = [
        {"status": "ok", "value": {"ai_features": [], "human_evidence": []}},
        {"status": "ok", "value": {}},
        {"status": "ok", "value": {"ai_features": None, "human_evidence": None}},
    ]
    for env in cases:
        got = _risk_of("reviewer", env)
        assert got == 0.0, f"{env} 应得硬编码 0.0，实得 {got!r}"
        assert isinstance(got, float), f"0.0 必须是 float，实得 {type(got).__name__}"


def test_u0c_risk_of_malformed_value_degrades_to_none_not_crash():
    """✅ 裁决 B（u0c-fix）：`value` 畸形（字符串/列表等预期外类型）时 `_risk_of`
    **降级返回 None**，不再抛 AttributeError。理由：本模块整体契约是"数据形态异常
    一律降级成标签/警告，绝不 500"（见 test_malformed_sections_degrade_to_labels_not_500），
    `_risk_of` 单独抛异常与该契约不一致。None = 「这条没有」（该路不参与融合），
    **不是 0.0**（0.0 = 「这条说没风险」）——语义之别由下方"两组皆空 → 恰好 0.0"用例分守。
    （上一单钉的是当时的实测行为 AttributeError；畸形输入本身一字未换。）"""
    from app.deai_signals import _risk_of

    for bad in ("oops", [1, 2], 3):
        got = _risk_of("reviewer", {"status": "ok", "value": bad})
        assert got is None, f"畸形 value {bad!r} 应降级为 None，实得 {got!r}"


# ---------------- 契约 E：logprobs 统计口径的降级形状 ----------------

def test_u0c_perplexity_unsupported_shows_as_not_collected_and_scores_nothing():
    """拿不到 logprobs（实测 MiniMax-M3 的形状）时：状态必须是 skipped（人话=不支持/未采集），
    value 必须为 None，detail 必须点名 logprobs——**不许给个默认分糊上去**。"""
    env = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, _raw_no_logprobs))
    assert env["status"] == "skipped", f"应为 skipped，实得 {env['status']}"
    assert env["value"] is None, f"降级档 value 必须是 None（哨兵分=伪造数据），实得 {env['value']!r}"
    assert "logprobs" in env["detail"], "必须写明是 logprobs 不可得，而不是含糊一句"


def test_u0c_skipped_perplexity_does_not_enter_fusion_weighting():
    """反向确认「不得参与打分加权」：只有规则路可用时，融合分必须等于纯规则风险分，
    困惑度那 0.2 权重**根本没进分母**。硬编码期望来自手工推演：
    rule value=60 → risk=100-60=40；仅 1 路 → 0.5*40/0.5 = 40.0。"""
    rule = {"signal": "rule", "status": "ok", "value": 60, "detail": "d"}
    ppl = {"signal": "perplexity", "status": "skipped", "value": None, "detail": "无 logprobs"}
    rev = {"signal": "reviewer", "status": "skipped", "value": None, "detail": "未调用"}
    fused = fuse_signals(rule, ppl, rev)
    assert fused["participating"] == ["rule"], f"skipped 路混进了参与路：{fused['participating']}"
    assert fused["score"] == 40.0, f"纯规则路分数应为硬编码 40.0，实得 {fused['score']!r}"
    assert "perplexity" in fused["detail"], "必须交代 perplexity 未参与"


def test_u0c_real_logprobs_are_consumed_not_always_degraded():
    """反向档（证明不是恒降级）：**假装有 logprobs** 的响应确实被采用并进了分数。
    手工推演：logprob -0.2/-2.2 → mean=-1.2 → ppl=exp(1.2)=3.32；
    risk = round(100/(1+3.32/50), 2) = 93.77；rule 60 → risk 40 →
    (0.5*40 + 0.2*93.77)/0.7 = 55.3628… → round(...,1) = 55.4。"""
    env = asyncio.run(perplexity_signal(_AI_TEXT, _FAKE_CONFIG, _raw_with_logprobs))
    assert env["status"] == "ok", f"有 logprobs 却仍降级：{env['status']}"
    assert env["value"] == 3.32, f"困惑度应取自真实 logprobs（手算 3.32），实得 {env['value']!r}"

    rule = {"signal": "rule", "status": "ok", "value": 60, "detail": "d"}
    rev = {"signal": "reviewer", "status": "skipped", "value": None, "detail": "未调用"}
    fused = fuse_signals(rule, env, rev)
    assert fused["participating"] == ["rule", "perplexity"]
    assert fused["score"] == 55.4, f"两路融合分应为硬编码 55.4，实得 {fused['score']!r}"


def test_u0c_default_off_probe_never_calls_the_endpoint():
    """端点默认 probe_logprobs=False → config=None → 该路直接 skipped，
    一次网络请求都不发（省钱 + 当前模型本来就探测不到）。"""
    env = asyncio.run(perplexity_signal(_AI_TEXT, None, _raw_boom))
    assert env["status"] == "skipped"
    assert env["value"] is None
    assert "未提供模型配置" in env["detail"]


# ---------------- 契约 S2：「文本太短」阈值的边界 ----------------
# 🔴 如实记录（实测 2026-11-16，grep 穷举两文件所得，非推测）：
#   anti_llm.detect() 第 95 行 `if len(text) < 50`；anti_llm.dimension_values() 第 280 行同款 50；
#   deai_signals.rule_signal() 不另设阈值，靠 detect() 的 issues[].type=="太短" 转 degenerate。
#   → 两条路的**长度阈值实测同为 50**，本仓库这一对里**没有** 50 vs 60 的分叉。
#   deai_signals.py:222 唯一的 60 是审校师提示词里「引文不超过 60 字」，与长度短路无关。
#   因此下面钉的是**共同边界 50**（49 不够 / 50 刚好够 / 51 略超三档）。
#   任一侧从 50 挪到 60，49/50 两档必红——这就是本条的牙。

def _s2_text(n: int) -> str:
    """构造**恰好 n 个字符**的文本（实测：len() 按码点计，中文一字算一）。
    用函数造而不是手数字符串——手抄的长度会漂，漂移的用例等于没有用例。"""
    base = "他把烟按灭在砖墙上。"  # 10 字
    filler = "风从巷口灌进来卷起糖纸一片两片三片四片五片六片七片八片九片十片"
    t = base * 4 + filler[: n - 40]
    assert len(t) == n, f"夹具构造失败：要 {n} 字，实得 {len(t)}"
    return t


_S2_T49 = _s2_text(49)
_S2_T50 = _s2_text(50)
_S2_T51 = _s2_text(51)


def test_u0c_short_text_boundary_is_exactly_fifty_chars():
    """钉住**当前实际行为**：len<50 → detect 短路 score=100 + issues 含「太短」；
    len==50 → 不再短路（走真检测）；len==51 同上。三档一起钉，阈值挪一档即红。"""

    d49 = detect(_S2_T49)
    assert d49["score"] == 100, "49 字应命中太短短路（哨兵 100）"
    assert any(i.get("type") == "太短" for i in d49["issues"]), "49 字必须带「太短」issue 标记"

    d50 = detect(_S2_T50)
    # 注意：50 字这条干净文本 score **仍是 100**（无命中），但 issues 里不再有「太短」——
    # 判据必须看 issues 标记而不是分数，否则短路和"真的干净"两种形状分不开。
    assert not any(i.get("type") == "太短" for i in d50["issues"]), \
        "50 字不该再被判太短——若这红了说明阈值被从 50 往上挪了"

    d51 = detect(_S2_T51)
    assert not any(i.get("type") == "太短" for i in d51["issues"]), "51 字（略超）同样不该短路"


def test_u0c_rule_signal_inherits_the_same_boundary():
    """检测路（deai_signals.rule_signal）与规则路口径一致性：49 字 → degenerate，
    50 字 → ok。**两路共用同一个 50**，任何一侧单独挪阈值都会在这里露馅。"""
    e49 = rule_signal(_S2_T49)
    assert e49["status"] == "degenerate", f"49 字应为 degenerate（哨兵未真正检测），实得 {e49['status']}"
    assert e49["value"] is None, "哨兵 100 不许作为 value 交给融合"

    e50 = rule_signal(_S2_T50)
    assert e50["status"] == "ok", f"50 字应正常参与打分，实得 {e50['status']}（阈值若被挪到 60 这里会红）"
    assert isinstance(e50["value"], (int, float))


def test_u0c_dimension_values_shares_the_boundary():
    """dimension_values 的第 280 行也是 `< 50`：49 字 → 9 维全 None；50 字 → 至少一维有值。
    这一档把 anti_llm 内部的**两处** 50 都各自钉住（detect 与 dimension_values 各一条腿）。"""
    v49 = dimension_values(_S2_T49)
    assert set(v49.keys()) == set(DIMENSION_NAMES)
    assert all(x is None for x in v49.values()), "49 字时 9 维必须全部不可得（None），不许用 0 冒充"

    v50 = dimension_values(_S2_T50)
    assert any(x is not None for x in v50.values()), \
        "50 字已越过阈值，至少要有一维算出值——若这红了说明 dimension_values 的 50 被挪到了 60"
