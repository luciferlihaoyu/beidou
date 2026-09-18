"""技能卡包（SKILL.md + references/assets/scripts）加载与注入测试。

背景：北斗最初只 vendored 了 11 张卡的 SKILL.md，卡正文里要求的
`references/*.md`、`assets/*.md`、`scripts/*.py` 全都没搬进来，而且加载逻辑
也从不读取它们——于是「按速查表加载参考文件」这一步在 web 应用里从未发生，
模型只能凭卡正文里的表格描述自由发挥，还可能谎称已加载。

这个文件的核心是**打包完整性测试**：卡正文里提到的每个文件都必须真实存在。
"""

from __future__ import annotations

import asyncio

import re

import pytest

from app import skilltools
from app.routers import skills

# 卡正文里以反引号引用的包内文件路径
_RE_REF = re.compile(r"`((?:references|assets|scripts)/[^`]+)`")


class TestPackagingIntegrity:
    """每张卡引用的文件必须真实存在——这是最初的 bug，必须有测试盯住。"""

    def test_all_cards_present(self):
        missing = [s for s in skills.CARD_META if skills._card_file(s) is None]
        assert missing == [], f"缺少技能卡文件：{missing}"

    def test_every_referenced_file_exists(self):
        # 卡正文里提到的脚本/清单/模板，若不存在，卡里写的流程就无法执行
        bad: list[str] = []
        for slug in skills.CARD_META:
            card = skills._parse_card(slug)
            assert card, slug
            for rel in set(_RE_REF.findall(card["body"])):
                if not (skills.CARD_DIR / slug / rel).is_file():
                    bad.append(f"{slug}: {rel}")
        assert bad == [], f"卡正文引用了不存在的文件：{bad}"

    def test_cards_have_substance_beyond_handbook(self):
        """每张卡都该有「比手册更强的东西」：参考清单/模板，或可在服务端执行的脚本。

        style-fingerprint 卡包上游只带了脚本（没有 references），它的价值就在
        可执行测量上——所以判定是「二者其一」，而不是一律要求有清单。
        """
        from app import skilltools

        weak = [
            slug
            for slug in skills.CARD_META
            if not any(d["kind"] in ("references", "assets") for d in skills.list_docs(slug))
            and not skilltools.available(slug)
        ]
        assert weak == [], f"这些卡只有手册、既无参考文件也无可用工具：{weak}"

    def test_docs_metadata_shape(self):
        for slug in skills.CARD_META:
            for d in skills.list_docs(slug):
                assert d["rel"].startswith(("references/", "assets/", "scripts/")), d
                assert d["chars"] > 0, d
                assert d["title"].strip(), d
                if d["kind"] == "scripts":
                    assert d["executable"] is False, d


class TestSelection:
    def test_must_load_docs_detected(self):
        """拆书卡明确规定 consistency-check.md 任何任务都必须加载。"""
        core = skills.core_docs("novel-deconstruction")
        assert "references/consistency-check.md" in core

    def test_must_load_not_triggered_by_distant_keyword(self):
        """报告模板只在「全书拆书」时用，不该被判定为无条件加载。"""
        assert "assets/report-template.md" not in skills.core_docs("novel-deconstruction")

    def test_golden_three_selects_only_relevant(self):
        picked = skills.select_docs("novel-deconstruction", "帮我分析这本书的黄金三章开篇")
        assert "references/golden-three-chapters.md" in picked
        assert "references/character-system.md" not in picked
        # 精确选择：不该把 6 个参考全塞进上下文
        assert len(picked) <= 3, picked

    def test_character_dimension(self):
        picked = skills.select_docs("novel-deconstruction", "拆解人物体系和关系网")
        assert "references/character-system.md" in picked
        assert "references/plot-structure.md" not in picked

    def test_full_book_loads_everything(self):
        picked = skills.select_docs("novel-deconstruction", "全书完整拆书")
        assert "assets/report-template.md" in picked
        assert len([p for p in picked if p.startswith("references/")]) == 6, picked

    def test_empty_instruction_loads_all(self):
        picked = skills.select_docs("novel-deconstruction", "")
        assert len([p for p in picked if p.startswith("references/")]) == 6

    def test_generic_instruction_loads_all(self):
        """AI 面板在用户没输入时发的是「请运用…技能开始工作」——没有维度关键词，
        此时只加载强制项会让五个维度清单全部缺席，必须走全量。"""
        picked = skills.select_docs("novel-deconstruction", "请运用「拆书分析」技能开始工作，并主动给出产出。")
        assert len([p for p in picked if p.startswith("references/")]) == 6
        assert "assets/report-template.md" in picked

    def test_explicit_request_wins(self):
        picked = skills.select_docs("novel-deconstruction", "随便", ["references/plot-structure.md"])
        assert "references/plot-structure.md" in picked
        # 强制项仍会并入
        assert "references/consistency-check.md" in picked

    def test_illegal_requested_ignored(self):
        picked = skills.select_docs("novel-deconstruction", "", ["../../etc/passwd", "nope.md"])
        assert all("passwd" not in p and p != "nope.md" for p in picked)
        assert picked  # 且不因非法输入而空手

    def test_unknown_slug(self):
        assert skills.select_docs("no-such-card", "随便") == []
        assert skills.core_docs("no-such-card") == []


class TestDocsBlock:
    def test_block_contains_files_and_manifest(self):
        rels = skills.select_docs("novel-deconstruction", "分析黄金三章")
        block, loaded, notes = skills.docs_block("novel-deconstruction", rels)
        assert block and loaded
        assert "【已加载的参考清单" in block
        assert "<<<FILE " in block and "<<<END FILE>>>" in block
        assert all(d["chars"] > 0 for d in loaded)
        assert notes == []  # 小卡包不该触发任何截断

    def test_scripts_never_injected(self):
        """脚本正文不注入——注入了只会诱导模型假装在跑。"""
        block, loaded, _ = skills.docs_block("deai-rewrite", ["scripts/ai_tells_scan.py"])
        assert block == "" and loaded == []

    def test_truncation_is_explicit(self, monkeypatch):
        monkeypatch.setattr(skills, "MAX_DOC_CHARS", 200)
        block, loaded, notes = skills.docs_block(
            "novel-deconstruction", ["references/golden-three-chapters.md"]
        )
        assert loaded[0]["truncated"] is True
        assert "已截断" in block and notes, (block[:200], notes)

    def test_total_budget_respected(self, monkeypatch):
        monkeypatch.setattr(skills, "MAX_TOTAL_DOC_CHARS", 300)
        rels = [d["rel"] for d in skills.list_docs("novel-deconstruction") if d["kind"] == "references"]
        block, loaded, notes = skills.docs_block("novel-deconstruction", rels)
        assert sum(d["chars"] for d in loaded) <= 300
        assert notes, "超限必须有说明，不能静默丢弃"


class TestCardBlock:
    def test_block_includes_handbook_and_docs(self):
        blk = skills.card_block("novel-deconstruction", task="分析黄金三章")
        assert "完整工作手册" in blk
        assert "【参考文件全文】" in blk
        assert "golden-three-chapters.md" in blk

    def test_block_states_scripts_are_not_executable(self):
        """必须明确声明脚本不可执行，避免模型谎称已运行脚本。"""
        blk = skills.card_block("novel-deconstruction", task="拆书")
        assert "无法执行这些脚本" in blk
        assert "不要声称已运行脚本" in blk

    def test_block_lists_skipped_docs(self):
        blk = skills.card_block("novel-deconstruction", task="分析黄金三章")
        assert "【本次未加载的文件】" in blk
        assert "plot-structure.md" in blk  # 未加载项要列出，便于模型按需索取

    def test_tool_output_injected_when_present(self):
        blk = skills.card_block("style-fingerprint", task="看文风", tool_output="【服务端工具输出·文风指纹】平均句长 12")
        assert "服务端工具输出" in blk

    def test_build_skill_prompt_appends_context_and_task(self):
        p = skills.build_skill_prompt("novel-deconstruction", "作品：《测试》", "拆黄金三章")
        assert "当前作品信息" in p and "作品：《测试》" in p
        assert "任务：拆黄金三章" in p

    def test_unknown_slug_raises_404(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            skills.card_block("no-such-card")


class TestSkillTools:
    """卡包脚本的服务端执行（白名单）。真实测量 ≫ 模型估计。"""

    def test_whitelist_only(self):
        assert skilltools.available("style-fingerprint")
        assert skilltools.available("webnovel-pace-analyzer")
        # 未白名单的卡不执行任何东西
        assert skilltools.available("deai-rewrite") is False
        assert skilltools.run_for_card("deai-rewrite", [("第一章", "正文" * 50)]) == ""
        assert skilltools.run_for_card("no-such-card", [("第一章", "正文")]) == ""

    def test_style_profile_real_measurement(self):
        text = "林昭推开门，风雪灌进领口。\n「你回来了。」她说。\n" * 40
        out = skilltools.run_for_card("style-fingerprint", [("第一章", text)])
        assert "文风指纹" in out
        assert "平均句长" in out
        assert "对话占比" in out
        assert "服务端" in out and "非模型估计" in out

    def test_pace_scan_per_chapter_table(self):
        text = "他握紧拳头，终于赢了。\n" * 60
        out = skilltools.run_for_card(
            "webnovel-pace-analyzer", [("第一章", text), ("第二章", text)]
        )
        assert "节奏扫描" in out
        assert "| 第一章 |" in out and "| 第二章 |" in out
        assert "汇总" in out

    def test_empty_text_returns_empty(self):
        assert skilltools.run_for_card("style-fingerprint", []) == ""
        assert skilltools.run_for_card("style-fingerprint", [("第一章", "   ")]) == ""

    def test_tool_failure_is_contained(self, monkeypatch):
        """工具抛异常不能拖垮整轮技能执行，但必须如实说明失败。"""

        def boom(*_a, **_k):
            raise RuntimeError("模拟脚本崩溃")

        monkeypatch.setattr(skilltools, "_load", lambda slug: boom)
        out = skilltools.run_for_card("style-fingerprint", [("第一章", "正文" * 100)])
        assert "执行失败" in out and "RuntimeError" in out

    def test_chapter_limit_applied(self, monkeypatch):
        monkeypatch.setattr(skilltools, "MAX_CHAPTERS", 2)
        text = "他握紧拳头，终于赢了。\n" * 60
        out = skilltools.run_for_card(
            "webnovel-pace-analyzer", [(f"第{i}章", text) for i in range(1, 6)]
        )
        assert "| 第1章 |" in out and "| 第2章 |" in out
        assert "| 第3章 |" not in out


# ---------------------------------------------------------------------------
# 拆书阅读上下文：模型没有文件系统，「读全书」由服务端按卡内规则代读。
# 用假 DB 会话直接测 reading_context 全链路（不依赖真库）。
# ---------------------------------------------------------------------------
from types import SimpleNamespace  # noqa: E402


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _FakeDB:
    """按查询实体分发假数据：select(Chapter) → 章节，select(Volume) → 卷。"""

    def __init__(self, chapters, vols):
        self._chapters = chapters
        self._vols = vols

    async def execute(self, q):
        entity = q.column_descriptions[0]["entity"]
        from app.models import Chapter, Volume

        return _FakeResult(self._chapters if entity is Chapter else self._vols)


def _mk_chapters(n, per_chapter_chars=500, vols=None):
    """造 n 章：正文为可识别文本；vols 是每章的 volume_id 列表（可 None）。"""
    out = []
    for i in range(1, n + 1):
        body = f"第{i}章正文。" + f"情节推进{i}。" * (per_chapter_chars // 8)
        out.append(
            SimpleNamespace(
                id=i,
                sort_order=i,
                title=f"试炼{i}",
                word_count=len(body),
                content=body,
                volume_id=vols[i - 1] if vols else None,
            )
        )
    return out


class TestReadingContext:
    def test_reading_cards_registry(self):
        assert "novel-deconstruction" in skills.READING_CARDS

    def test_golden_three_full_text(self):
        chs = _mk_chapters(6)
        out = asyncio.run(skills.reading_context(_FakeDB(chs, []), 1))
        assert "【本书阅读上下文" in out
        assert "【章节索引】" in out and "第1章 试炼1" in out and "第6章" in out
        # 前三章给全文
        assert out.count("· 全文】") == 3
        assert "第1章正文。" in out
        # 第 4-6 章不在黄金三章里，但会作为全书抽样的 首/末/最长 出现
        assert "· 抽样】" in out
        assert "未覆盖" in out  # 必须交代诚实标注义务

    def test_empty_novel_returns_empty(self):
        assert asyncio.run(skills.reading_context(_FakeDB([], []), 1)) == ""

    def test_volume_sampling_skips_golden(self):
        # 两卷各 8 章：黄金三章吃掉卷一前 3 章，卷一剩 5 章 → 抽样；卷二 8 章 → 抽样
        vols = [1] * 8 + [2] * 8
        chs = _mk_chapters(16, vols=vols)
        vol_objs = [
            SimpleNamespace(id=1, title="第一卷 风起", sort_order=1),
            SimpleNamespace(id=2, title="第二卷 云涌", sort_order=2),
        ]
        out = asyncio.run(skills.reading_context(_FakeDB(chs, vol_objs), 1))
        assert "第一卷 风起 · " in out and "第二卷 云涌 · " in out
        # 黄金三章（第1-3章）不应再以抽样身份出现
        assert "第一卷 风起 · 第1章" not in out

    def test_long_chapter_truncated_with_note(self, monkeypatch):
        monkeypatch.setattr(skills, "GOLDEN_CAP", 300)
        chs = _mk_chapters(4, per_chapter_chars=1200)
        out = asyncio.run(skills.reading_context(_FakeDB(chs, []), 1))
        assert "超长截断" in out and "已截断" in out

    def test_total_budget_drops_samples_first(self, monkeypatch):
        monkeypatch.setattr(skills, "MAX_READING_CHARS", 2500)
        chs = _mk_chapters(10, per_chapter_chars=900)
        out = asyncio.run(skills.reading_context(_FakeDB(chs, []), 1))
        assert "总量上限" in out  # 必须显式说明，不能静默丢
        assert out.count("· 全文】") == 3  # 黄金三章优先保住

    def test_index_capped_with_note(self, monkeypatch):
        monkeypatch.setattr(skills, "INDEX_CAP_CHAPTERS", 10)
        chs = _mk_chapters(30, per_chapter_chars=250)
        out = asyncio.run(skills.reading_context(_FakeDB(chs, []), 1))
        assert "仅显示前 10 行" in out and "共 30 章" in out
        assert "第30章" not in out.split("【章节索引】")[1].split("【")[0]

    def test_small_novel_no_sampling_noise(self):
        # 只有 4 章：黄金三章盖住后只剩 1 章，不触发抽样，也不该有抽取说明噪声
        chs = _mk_chapters(4)
        out = asyncio.run(skills.reading_context(_FakeDB(chs, []), 1))
        assert out.count("· 抽样】") == 0
        assert "【抽取说明】" not in out
