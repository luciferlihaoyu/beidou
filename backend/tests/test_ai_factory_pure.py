"""AI 工厂关键纯函数单测（小范围单文件，符合容器规矩：不跑全量套件）。

覆盖：
- anti_llm.detect：AI 味文本应低分，人味文本应高分
- textlint.lint：规范文本满分，问题文本检出
- ai_import._split_chapters：标题行分章 / 硬切兜底
- ai_factory._parse_json / _stamp_plot_arcs / _hook_alerts / 路由格式解析
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.anti_llm import detect  # noqa: E402
from app.nightly import split_long_chapter  # noqa: E402
from app.textlint import lint  # noqa: E402
from app.routers.ai_factory import _hook_alerts, _parse_json, _stamp_plot_arcs  # noqa: E402
from app.routers.ai_import import _split_chapters  # noqa: E402


class TestAntiLlm:
    def test_ai_flavored_text_scores_low(self):
        bad = "他非常高兴，然后突然明白了一切。空气仿佛凝固了，时间似乎停止了。" * 10
        assert detect(bad)["score"] < 70

    def test_human_text_scores_high(self):
        good = "他把烟按灭在砖墙上。「走吧。」她说。风从巷口灌进来，卷起地上的糖纸。" * 10
        assert detect(good)["score"] >= 70


class TestTextLint:
    def test_clean_text_full_score(self):
        r = lint("他跑得飞快。我们一起去看了看，她说：「好。」夕阳渐渐沉入山脊。")
        assert r["score"] == 100
        assert r["issues"] == []

    def test_detects_repeat_and_sensitive(self):
        r = lint("我们我们一起去赌场。")
        types = {i["type"] for i in r["issues"]}
        assert "重复词" in types and "敏感词" in types

    def test_detects_typo(self):
        r = lint("他按装了新机器。")
        assert any(i["type"] == "错别字" for i in r["issues"])


class TestSplitChapters:
    def test_mixed_title_patterns(self):
        text = "前言内容。\n\n第一章 觉醒\n正文一。\nChapter 2 Battle\nEnglish body.\n第一百零三章 决战\n正文三。"
        chs = _split_chapters(text)
        titles = [c["title"] for c in chs]
        assert "第一章 觉醒" in titles and "Chapter 2 Battle" in titles and "第一百零三章 决战" in titles
        assert chs[0]["title"] == "序章"

    def test_fallback_hard_split(self):
        chs = _split_chapters("x" * 9000)
        assert len(chs) == 3 and chs[0]["title"] == "第1章"

    def test_no_empty_chapters(self):
        chs = _split_chapters("第一章 标题\n\n第二章 标题2\n内容")
        assert all(c["content"] for c in chs)


class TestParseJson:
    def test_plain_json(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_surrounding_text(self):
        assert _parse_json('好的，结果如下：\n{"a": 1}\n以上。') == {"a": 1}


class TestPlotArcStamping:
    def test_new_arc_gets_current_chapter(self):
        out = json.loads(_stamp_plot_arcs("", [{"title": "神秘玉佩", "status": "埋设中"}], 5))
        assert out[0]["planted_chapter"] == 5

    def test_existing_arc_inherits_stamp(self):
        old = json.dumps([{"title": "神秘玉佩", "status": "埋设中", "planted_chapter": 3}])
        out = json.loads(_stamp_plot_arcs(old, [{"title": "神秘玉佩", "status": "推进中"}], 8))
        assert out[0]["planted_chapter"] == 3


class TestHookAlerts:
    def test_overdue_hook_alerted(self):
        arcs = json.dumps([
            {"title": "身世之谜", "status": "埋设中", "planted_chapter": 2},
            {"title": "已回收的", "status": "已回收", "planted_chapter": 1},
        ])
        alerts = _hook_alerts(arcs, 20)
        assert len(alerts) == 1
        assert alerts[0]["title"] == "身世之谜"
        assert alerts[0]["level"] == "overdue"
        assert alerts[0]["age"] == 18

    def test_recent_hook_not_alerted(self):
        arcs = json.dumps([{"title": "新伏笔", "status": "埋设中", "planted_chapter": 18}])
        assert _hook_alerts(arcs, 20) == []


class TestRouteFormat:
    def test_route_field_parsing(self):
        # "3@model" 格式的解析逻辑（_pick_config 内的 partition）
        for field, expect_id, expect_model in [
            ("3", "3", ""),
            ("3@deepseek-v4", "3", "deepseek-v4"),
            ("", "", ""),
        ]:
            cfg, _, model = field.partition("@")
            assert cfg == expect_id and model == expect_model


class TestAntiLlmStructural:
    """M9 结构层检测：上帝视角/章末总结体/三连排比/套词密度。"""

    def test_god_view_and_ending_detected(self):
        text = "他看着远方。殊不知，命运的齿轮已经转动。" + "他走在路上。" * 20 + "这一夜，注定无人入眠。"
        r = detect(text)
        types = {i["type"] for i in r["issues"]}
        assert "上帝视角" in types and "章末总结体" in types

    def test_triple_parallel_detected(self):
        text = "他不是害怕，也不是退缩，而是在等待时机。" + "风吹过。" * 20
        assert any(i["type"] == "三连排比" for i in detect(text)["issues"])

    def test_cliche_density_detected(self):
        text = ("他深吸一口气，眼中闪过一丝光，嘴角勾起一抹笑，仿佛一切尽在掌握。" * 6)
        assert any(i["type"] == "套词密度" for i in detect(text)["issues"])

    def test_clean_text_no_structural_flags(self):
        good = "他把烟按灭在墙上。「走吧。」她说。风从巷口灌进来。" * 10
        types = {i["type"] for i in detect(good)["issues"]}
        assert "上帝视角" not in types and "章末总结体" not in types


class TestTextLintPlatform:
    def test_platform_extra_words(self):
        from app.textlint import lint

        text = "他们在地下赌场碰头。"
        plain = lint(text)
        fanqie = lint(text, platform="fanqie")
        assert fanqie["score"] < plain["score"]

    def test_custom_words(self):
        from app.textlint import lint

        r = lint("他开启了阿尔法系统。", custom_words=["阿尔法"])
        assert any("阿尔法" in i["detail"] for i in r["issues"])

    def test_unknown_platform_falls_back(self):
        from app.textlint import lint

        assert lint("干净文本。", platform="nonexistent")["score"] == 100


class TestUsageAndNightly:
    """M13 成本账本 + 夜间连跑基础逻辑。"""

    def test_estimate_tokens(self):
        from app.routers.ai_factory import _estimate_tokens

        assert _estimate_tokens("汉" * 1700) == 1000
        assert _estimate_tokens("") == 0

    def test_record_usage_accumulates(self):
        from app.routers.ai_factory import _record_usage

        class FakeProject:
            tokens_prompt = 100
            tokens_completion = 50

        p = FakeProject()
        _record_usage(p, 200, 80)
        assert p.tokens_prompt == 300 and p.tokens_completion == 130
        _record_usage(p, -5, 0)  # 负数防御
        assert p.tokens_prompt == 300

    def test_nightly_window_constant(self):
        from app.nightly import NIGHTLY_WINDOW_HOURS

        assert NIGHTLY_WINDOW_HOURS == (2, 3, 4)


class TestSplitLongChapter:
    """超长自动分章纯函数：贪心装填 + 场景符边界 + 防失控护栏。"""

    # 每个重复单元 19 字（无空白）：他提着灯走进雨里，巷口的风铃响了三声。
    UNIT = "他提着灯走进雨里，巷口的风铃响了三声。"

    @classmethod
    def _para(cls, reps: int) -> str:
        return cls.UNIT * reps

    def test_normal_greedy_split_two_parts(self):
        # target=1000，三段各约 532 字：前两段装一桶（1064≥1000 封口），尾段一桶
        text = "\n\n".join([self._para(28), self._para(28), self._para(28)])
        parts = split_long_chapter(text, 1000)
        assert len(parts) == 2
        assert self._para(28) in parts[0] and parts[1] == self._para(28)

    def test_scene_break_preferred_as_boundary(self):
        # 桶已过半（600 ≥ 500）时遇到场景分隔符行优先封口，分隔符归入下一桶开头
        text = "\n\n".join([self._para(32), "***", self._para(32)])
        parts = split_long_chapter(text, 1000)
        assert len(parts) == 2
        assert "***" not in parts[0] and parts[1].startswith("***")

    def test_gives_up_when_too_many_parts(self):
        # target=500，十段各 500+ 字会拆出 10 桶 > 4，防失控不拆
        text = "\n\n".join([self._para(27)] * 10)
        parts = split_long_chapter(text, 500)
        assert len(parts) == 1 and parts[0] == text

    def test_gives_up_when_fragmented(self):
        # 尾桶 304 字 < target*0.4（400），拆得太碎不如不拆
        text = "\n\n".join([self._para(53), self._para(16)])
        parts = split_long_chapter(text, 1000)
        assert len(parts) == 1 and parts[0] == text


class TestDeconstructReference:
    """M14 拆书学习：范式笔记 → prompt 注入块。"""

    def _proj(self, note):
        import json

        class P:
            reference_json = json.dumps(note, ensure_ascii=False) if note is not None else ""

        return P()

    def test_empty_reference_returns_blank(self):
        from app.routers.ai_deconstruct import reference_prompt_block

        assert reference_prompt_block(self._proj(None)) == ""
        assert reference_prompt_block(self._proj({})) == ""

    def test_reference_block_contains_fields(self):
        from app.routers.ai_deconstruct import reference_prompt_block

        blk = reference_prompt_block(
            self._proj(
                {
                    "title": "示例书",
                    "worldview_framework": "宗门林立+位面晋升",
                    "power_system": "九阶斗气",
                    "pacing": "3章一小高潮",
                    "character_config": [{"role": "主角", "archetype": "废柴逆袭", "traits": "家族废物"}],
                    "hooks": ["开局退婚打脸"],
                    "avoid": ["退婚流已用烂"],
                    "borrow_notes": "借结构换皮",
                }
            )
        )
        for token in ("宗门林立", "九阶斗气", "废柴逆袭", "开局退婚打脸", "退婚流已用烂", "必须全新原创"):
            assert token in blk

    def test_broken_json_returns_blank(self):
        from app.routers.ai_deconstruct import reference_prompt_block

        class Bad:
            reference_json = "{不是合法 JSON"

        assert reference_prompt_block(Bad()) == ""

    def test_sampling_keeps_head_and_tail(self):
        from app.routers.ai_deconstruct import SAMPLE_CHARS, _sample

        text = "甲" * 200_000 + "乙" * 100
        out = _sample(text)
        assert len(out) <= SAMPLE_CHARS + 20
        assert out.startswith("甲") and out.rstrip().endswith("乙")


class TestNightlyWindowTimezone:
    """夜跑窗口按作者时区判定（容器多是 UTC，避免凌晨窗口跑到北京上午）。"""

    def test_default_window_hours(self):
        import os

        os.environ.pop("BEIDOU_NIGHTLY_HOURS", None)
        import importlib

        from app import nightly

        importlib.reload(nightly)
        assert nightly.NIGHTLY_WINDOW_HOURS == (2, 3, 4)
        assert nightly._nightly_now().tzinfo is not None  # 默认带时区（Asia/Shanghai）

    def test_env_override_hours(self):
        import importlib
        import os

        os.environ["BEIDOU_NIGHTLY_HOURS"] = "1,23,99,x"
        from app import nightly

        importlib.reload(nightly)
        assert nightly.NIGHTLY_WINDOW_HOURS == (1, 23)  # 非法值被丢弃
        os.environ.pop("BEIDOU_NIGHTLY_HOURS")
        importlib.reload(nightly)

    def test_local_and_bad_tz_fall_back(self):
        import importlib
        import os

        from app import nightly

        os.environ["BEIDOU_NIGHTLY_TZ"] = "local"
        importlib.reload(nightly)
        assert nightly._nightly_now().tzinfo is None  # 容器本地时间

        os.environ["BEIDOU_NIGHTLY_TZ"] = "No/Such_Zone"
        importlib.reload(nightly)
        assert nightly._nightly_now().tzinfo is None  # 非法时区退回本地，不抛错

        os.environ.pop("BEIDOU_NIGHTLY_TZ")
        importlib.reload(nightly)


class TestNightlyDedup:
    """夜跑判重：修 Critical（写入侧曾用 UTC 日期，判定侧用作者时区日期 → 判重永远失效）。

    契约：同一晚窗口内多次 tick 只能跑一次；次日窗口必须能再跑。
    """

    def _now(self, hour=3, tz="Asia/Shanghai"):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        return datetime(2026, 9, 17, hour, 0, tzinfo=ZoneInfo(tz))

    def test_same_night_repeat_blocked_by_ts(self):
        from app.nightly import _already_ran_this_night

        now = self._now(3)
        last = json.dumps({"date": "2026-09-17", "ts": now.timestamp() - 1800})  # 半小时前
        assert _already_ran_this_night(last, now, "2026-09-17") is True

    def test_next_night_allowed_by_ts(self):
        from app.nightly import _already_ran_this_night

        now = self._now(3)
        last = json.dumps({"date": "2026-09-16", "ts": now.timestamp() - 24 * 3600})
        assert _already_ran_this_night(last, now, "2026-09-17") is False

    def test_legacy_utc_date_within_window_blocked(self):
        """历史数据（无 ts，按 UTC 写的日期）：窗口内跑过后，后续 tick 必须判为已跑。

        这是被修复的原始 bug：2 点 CST = 前一天 18 点 UTC，写入 date=昨天，
        而判定侧算 today=今天，旧实现直接漏判 → 每 30 分钟重复跑。
        """
        from app.nightly import _already_ran_this_night

        now = self._now(3)
        assert _already_ran_this_night(json.dumps({"date": "2026-09-16"}), now, "2026-09-17") is True

    def test_legacy_old_date_allowed(self):
        from app.nightly import _already_ran_this_night

        now = self._now(3)
        assert _already_ran_this_night(json.dumps({"date": "2026-09-14"}), now, "2026-09-17") is False

    def test_garbage_and_empty_are_not_treated_as_ran(self):
        from app.nightly import _already_ran_this_night

        now = self._now(3)
        for bad in (None, "", "{不是JSON", "[]", json.dumps({"date": 123})):
            assert _already_ran_this_night(bad, now, "2026-09-17") is False

    def test_write_side_uses_author_timezone_date(self):
        """写入侧的日期必须与判定侧同源：断言代码里不再出现 datetime.now(timezone.utc) 写 date。"""
        from pathlib import Path

        src = Path("app/nightly.py").read_text(encoding="utf-8")
        report_block = src[src.index("report = {"): src.index("report = {") + 400]
        assert '"date": _nightly_now()' in report_block
        assert 'datetime.now(timezone.utc).strftime("%Y-%m-%d")' not in report_block
        assert '"ts": _nightly_now().timestamp()' in report_block


class TestDeconstructGuards:
    """拆书端点的护栏：频率限制 + 采样保尾（天演审查标为无测试覆盖的高风险函数）。"""

    def _fresh_user(self, uid: int):
        from app.routers.ai_deconstruct import _recent_calls

        _recent_calls.pop(uid, None)
        return uid

    def test_first_call_passes_second_is_429(self):
        from fastapi import HTTPException

        from app.routers.ai_deconstruct import _check_rate_limit

        uid = self._fresh_user(900001)
        _check_rate_limit(uid)  # 不抛即通过
        import pytest

        with pytest.raises(HTTPException) as ei:
            _check_rate_limit(uid)
        assert ei.value.status_code == 429

    def test_hourly_cap(self):
        import time

        import pytest
        from fastapi import HTTPException

        from app.routers.ai_deconstruct import (
            _RATE_LIMIT_MAX_PER_HOUR,
            _check_rate_limit,
            _recent_calls,
        )

        uid = self._fresh_user(900002)
        _recent_calls[uid] = [time.time() - 600] * _RATE_LIMIT_MAX_PER_HOUR
        with pytest.raises(HTTPException) as ei:
            _check_rate_limit(uid)
        assert ei.value.status_code == 429

    def test_expired_records_do_not_lock_user_out(self):
        import time

        from app.routers.ai_deconstruct import _check_rate_limit, _recent_calls

        uid = self._fresh_user(900003)
        _recent_calls[uid] = [time.time() - 3700] * 99  # 一小时前的旧记录应被清理
        _check_rate_limit(uid)  # 不应抛错

    def test_sample_preserves_tail_of_client_upload(self):
        """客户端上传「首 30 万 + 尾 6 万」时，尾部必须能进模型（曾被服务端盲截断切掉）。"""
        from app.routers.ai_deconstruct import MAX_DECONSTRUCT_CHARS, _sample

        head, tail = "甲" * 300_000, "乙" * 60_000
        uploaded = head + "\n\n……（中间省略）……\n\n" + tail
        assert len(uploaded) <= MAX_DECONSTRUCT_CHARS  # 硬上限必须容得下客户端采样结果
        out = _sample(uploaded)
        assert out.startswith("甲")
        assert out.rstrip().endswith("乙")
