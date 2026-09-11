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
