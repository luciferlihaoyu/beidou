"""平台合规检测（app/censor.py）单测。

重点覆盖三类容易出错的地方：
1. 分级是否正确（红线不该被降级、正常文本不该被误报）
2. 词表/类别/模式三处定义的一致性（曾因 RULES 与 CATEGORY_META 类别名不一致
   导致 KeyError，会让 lint 端点直接 500）
3. 误报控制（正常英文词、空格排版不该被判绕审）
"""

from __future__ import annotations

import re

import pytest

from app import censor


class TestCategoryConsistency:
    """防止类别名漂移：任何地方用到的类别都必须在 CATEGORY_META 里有定义。"""

    def test_rules_categories_are_defined(self):
        missing = [c for c in censor.RULES if c not in censor.CATEGORY_META]
        assert missing == [], f"RULES 里的类别未在 CATEGORY_META 定义：{missing}"

    def test_pattern_categories_are_defined(self):
        missing = [c for c, _, _ in censor.PATTERNS if c not in censor.CATEGORY_META]
        assert missing == [], f"PATTERNS 里的类别未定义：{missing}"

    def test_safe_compounds_reference_real_words(self):
        known = {w for words in censor.RULES.values() for w in words}
        unknown = [w for w in censor.SAFE_COMPOUNDS if w not in known]
        assert unknown == [], f"白名单引用了不存在的词：{unknown}"

    def test_every_category_has_why_and_how(self):
        for cat, meta in censor.CATEGORY_META.items():
            assert meta["why"].strip(), cat
            assert meta["how"].strip(), cat
            assert meta["level"] in censor.LEVELS, cat

    def test_every_pattern_compiles_and_has_note(self):
        for cat, pat, note in censor.PATTERNS:
            assert isinstance(pat, re.Pattern), cat
            assert note.strip(), cat


class TestLevels:
    def test_hard_redlines_are_block(self):
        cases = {
            "他学会了制毒配方": "毒品违禁",
            "那是一段淫秽的描写": "淫秽色情",
            "这是个邪教组织": "邪教迷信",
            "他迷上了网络赌博": "赌博",
        }
        for text, cat in cases.items():
            issues = censor.check(text)
            hit = [i for i in issues if i["category"] == cat]
            assert hit, f"{text} 未命中 {cat}"
            assert hit[0]["level"] == "block", f"{cat} 等级被降级：{hit[0]['level']}"

    def test_platform_order_issues_are_fix(self):
        for text, cat in (
            ("今天就到这里，求收藏求推荐票", "正文作者话"),
            ("有问题可以加我微信 abc12345", "引流联系方式"),
            ("待补\n................", "乱码占位"),
        ):
            issues = censor.check(text)
            hit = [i for i in issues if i["category"] == cat]
            assert hit, f"{text} 未命中 {cat}"
            assert hit[0]["level"] == "fix"

    def test_context_dependent_is_review(self):
        issues = censor.check("他喝了一瓶可口可乐")
        assert issues and issues[0]["level"] == "review"
        assert issues[0]["category"] == "现实品牌"

    def test_severity_mapping_keeps_old_contract(self):
        for level, sev in (("block", "high"), ("fix", "medium"), ("review", "low")):
            assert censor.LEVELS and censor.level_of_severity(sev) == level

    def test_issues_sorted_block_first(self):
        issues = censor.check("他喝了一瓶可口可乐，还加我微信 abc12345，并在研究制毒配方")
        levels = [i["level"] for i in issues]
        order = {"block": 0, "fix": 1, "review": 2}
        assert levels == sorted(levels, key=lambda l: order[l]), levels


class TestFalsePositives:
    def test_clean_text_has_no_issues(self):
        assert censor.check("他推开门，风雪灌进领口。远处的钟楼敲了三下。") == []

    def test_normal_english_words_not_flagged(self):
        for text in ("他打开 Python 写了几行代码", "他打开Python写了几行代码", "这个 APP 很好用"):
            hits = [i for i in censor.check(text) if i["category"] == "绕审写法"]
            assert hits == [], f"{text} 被误判绕审：{hits}"

    def test_space_separated_chinese_not_flagged(self):
        """空格排版（他 说 道）不该被判为拆字绕审。"""
        hits = [i for i in censor.check("他 说 道 ： 好 吧") if i["category"] == "绕审写法"]
        assert hits == []

    def test_pinyin_obfuscation_is_flagged(self):
        hits = [i for i in censor.check("那个人真是个shabi，居然还敢来。") if i["category"] == "绕审写法"]
        assert hits, "拼音替代应被提示（平台明确禁止为躲审写不规范）"

    def test_symbol_split_obfuscation_is_flagged(self):
        hits = [i for i in censor.check("他在研*究*炸*药") if i["category"] == "绕审写法"]
        assert hits

    def test_same_word_reported_once(self):
        issues = censor.check("制毒。制毒。制毒。")
        assert len([i for i in issues if "制毒" in i["detail"]]) == 1


class TestPatterns:
    def test_contact_info_patterns(self):
        cases = [
            "加群 123456789",
            "微信号：abc12345",
            "扫码下面二维码",
            "联系我 13812345678",
            "邮箱 test@example.com",
            "详见 https://example.com/x",
        ]
        for text in cases:
            hits = [i for i in censor.check(text) if i["category"] == "引流联系方式"]
            assert hits, f"未识别引流：{text}"

    def test_placeholder_patterns(self):
        for text in ("未完待续的占位…… TODO", "*****", "本章未完，稍后补全", "待补", "……此处省略"):
            hits = [i for i in censor.check(text) if i["category"] == "乱码占位"]
            assert hits, f"未识别占位/乱码：{text}"

    def test_minor_plus_sexual_is_block(self):
        hits = [i for i in censor.check("那个少女遭到了侵犯") if i["category"] == "未成年不当"]
        assert hits and hits[0]["level"] == "block"

    def test_duplicate_paragraph_detected(self):
        long_line = "他走进屋子，看见桌上放着一封信，信封上写着他的名字。" * 2
        issues = censor.check(f"{long_line}\n{long_line}")
        assert any(i["category"] == "重复填充" for i in issues)

    def test_long_unbroken_paragraph_flagged_as_water(self):
        issues = censor.check("他" + "说了一句话，然后继续往前走。" * 40)
        assert any(i["category"] == "水文特征" for i in issues)


class TestCustomWordsAndPlatform:
    def test_custom_words_reported(self):
        issues = censor.check("这里有违禁词甲乙", custom_words=["违禁词甲乙"])
        hit = [i for i in issues if i["category"] == "自定义"]
        assert hit and "违禁词甲乙" in hit[0]["detail"]

    def test_platform_extra_rules_merged_without_crash(self):
        issues = censor.check("黑帮火并", extra_rules={"涉黑": ["黑帮火并"]})
        assert issues, "平台加严词未生效"
        assert issues[0]["level"] in censor.LEVELS

    def test_empty_text(self):
        assert censor.check("") == []


class TestTextlintIntegration:
    """textlint.lint 的对外契约：score/issues 字段必须保持，新增 level/summary。"""

    def test_contract_preserved(self):
        from app.textlint import lint

        r = lint("他学会了制毒配方。", platform="fanqie")
        assert isinstance(r["score"], int) and 0 <= r["score"] <= 100
        assert isinstance(r["issues"], list)
        for i in r["issues"]:
            assert {"type", "severity", "detail"} <= set(i), i
            assert i["severity"] in {"high", "medium", "low"}

    def test_summary_and_note_added(self):
        from app.textlint import lint

        r = lint("制毒配方。求收藏。他喝可口可乐。")
        assert r["summary"]["block"] >= 1
        assert r["summary"]["fix"] >= 1
        assert r["summary"]["review"] >= 1
        assert "不保证" in r["note"] or "过审" in r["note"]

    def test_block_lowers_score_more_than_review(self):
        from app.textlint import lint

        hard = lint("他研究制毒配方。")["score"]
        soft = lint("他喝了一瓶可口可乐。")["score"]
        assert hard < soft

    def test_custom_words_still_work_through_lint(self):
        from app.textlint import lint

        r = lint("甲乙丙丁", custom_words=["甲乙丙丁"])
        assert any("自定义" == i.get("category") for i in r["issues"])


class TestPlatformProfiles:
    """平台词库与全局类别的一致性 + 升档机制。曾因「暴恐」被误映射到「涉政有害」
    导致同一处证据被归错类别并重复上报。"""

    def test_every_platform_category_resolves(self):
        from app.textlint import PLATFORM_PROFILES

        for key, prof in PLATFORM_PROFILES.items():
            for cat in prof.get("extra", {}):
                resolved = censor.resolve_category(cat)
                assert resolved in censor.CATEGORY_META, f"{key}/{cat} → {resolved} 未定义"

    def test_escalate_categories_exist(self):
        from app.textlint import PLATFORM_PROFILES

        for key, prof in PLATFORM_PROFILES.items():
            for cat in prof.get("escalate", []):
                assert cat in censor.CATEGORY_META, f"{key}/escalate 里的 {cat} 不是有效类别"
                assert censor.CATEGORY_META[cat]["level"] != "block", (
                    f"{key}: {cat} 本身已是 block，无需升档"
                )

    def test_alias_keeps_semantics(self):
        """「暴恐」应归到暴力细节，不能归到涉政有害。"""
        assert censor.resolve_category("暴恐") == "暴力细节"
        assert censor.resolve_category("涉黑") == "涉黑违法"
        assert censor.resolve_category("色情") == "淫秽色情"
        assert censor.resolve_category("完全没见过的类别") in censor.CATEGORY_META

    def test_platform_hardening_does_not_mislabel(self):
        from app.textlint import lint

        r = lint("他详细描写了分尸的过程", platform="fanqie")
        cats = {i["category"] for i in r["issues"]}
        assert "涉政有害" not in cats, f"分尸被误判涉政：{cats}"
        assert "暴力细节" in cats

    def test_escalation_same_evidence_higher_level(self):
        from app.textlint import lint

        text = "他详细描写了分尸的过程"
        base = [i for i in lint(text)["issues"] if i["category"] == "暴力细节"][0]
        hard = [i for i in lint(text, platform="fanqie")["issues"] if i["category"] == "暴力细节"][0]
        order = {"review": 0, "fix": 1, "block": 2}
        assert order[hard["level"]] > order[base["level"]], (base["level"], hard["level"])
        assert "加严" in hard["detail"]

    def test_no_duplicate_issue_for_same_word(self):
        """平台词与全局词重合时只报一次（此前会重复上报）。"""
        from app.textlint import lint

        r = lint("分尸", platform="fanqie")
        assert len([i for i in r["issues"] if "分尸" in i["detail"]]) == 1, r["issues"]


class TestLibraryCoverage:
    """词库规模下限：任务是「先完善一批」，用断言防止回退到很薄的词表。"""

    def test_category_count(self):
        assert len(censor.RULES) >= 15, f"类别只有 {len(censor.RULES)} 类"

    def test_word_count(self):
        total = sum(len(v) for v in censor.RULES.values())
        assert total >= 150, f"词条只有 {total} 条"

    def test_no_empty_or_duplicate_words_within_category(self):
        for cat, words in censor.RULES.items():
            assert words, cat
            assert len(words) == len(set(words)), f"{cat} 有重复词"
            assert all(w.strip() for w in words), cat

    def test_word_not_in_two_categories(self):
        """同一个词出现在两个类别会造成重复上报。"""
        seen: dict[str, str] = {}
        dup: list[str] = []
        for cat, words in censor.RULES.items():
            for w in words:
                if w in seen:
                    dup.append(f"{w}({seen[w]}/{cat})")
                seen[w] = cat
        assert dup == [], f"跨类别重复词：{dup}"

    def test_platform_count(self):
        from app.textlint import PLATFORM_CHOICES

        assert len(PLATFORM_CHOICES) >= 6


class TestExportReport:
    """导出稿包里的《敏感词终检报告》：用户真正拿到的那份交付物。

    这条链路此前没有任何测试，报告又是「投稿前最后一关」，值得盯住。
    """

    def _pack(self, chapters):
        from types import SimpleNamespace

        from app.routers.ai_extras import _build_export_pack

        p = SimpleNamespace(
            platform="fanqie",
            custom_words="",
            synopsis_json="",
            cover_prompt="",
            book_spec_json="",
            genre="都市",
            global_summary="",
            character_state="",
            plot_arcs="",
        )
        novel = SimpleNamespace(title="测试书", author="作者", status="连载中", genre="都市")
        groups = [(None, chapters)]
        return _build_export_pack(p, novel, groups)

    def _report(self, chapters) -> str:
        import io
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(self._pack(chapters)))
        return zf.read("敏感词终检报告.txt").decode()

    def _chapter(self, number, title, content, status="done"):
        from types import SimpleNamespace

        return (
            SimpleNamespace(title=title, content=content, word_count=len(content), status=status),
            number,
        )

    def test_report_has_levels_and_advice(self):
        rep = self._report([self._chapter(1, "第一章", "他研究了制毒配方。")])
        assert "🔴 硬红线" in rep, rep[:400]
        assert "合规改法" in rep, rep[:400]
        assert "问题类别汇总" in rep
        assert "结论：" in rep

    def test_verdict_rejects_despite_high_score(self):
        """只有 1 项红线时平均分可能仍 ≥90，但结论必须是「不宜投稿」。"""
        rep = self._report(
            [
                self._chapter(1, "第一章", "他研究了制毒配方。" * 1),
                self._chapter(2, "第二章", "他推开门，风雪灌进领口。远处的钟楼敲了三下。"),
            ]
        )
        assert "❌" in rep and "硬红线" in rep

    def test_report_states_its_limits(self):
        """报告必须写明它不是平台确切词库、待复核项需人工判断。"""
        rep = self._report([self._chapter(1, "第一章", "他推开门，风雪灌进领口。")])
        assert "不是任何平台的确切词库" in rep
        assert "待复核" in rep

    def test_clean_chapter_reports_pass(self):
        rep = self._report([self._chapter(1, "第一章", "他推开门，风雪灌进领口。远处的钟楼敲了三下。")])
        assert "✅ 全部检测章节未命中敏感词/规范问题。" in rep

    def test_only_done_chapters_are_checked(self):
        rep = self._report(
            [
                self._chapter(1, "第一章", "他研究了制毒配方。", status="done"),
                self._chapter(2, "第二章", "他研究了制毒配方。", status="draft"),
            ]
        )
        assert "done 章节 1 章 / 全书共 2 章" in rep

    def test_chapter_txt_files_are_written(self):
        import io
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(self._pack([self._chapter(7, "第七章", "正文内容在这里。")])))
        names = zf.namelist()
        assert any(n.startswith("正文/") and "第007章" in n for n in names), names


class TestReportFormatting:
    def test_format_groups_by_level_and_dedups_advice(self):
        from app.censor import format_issues

        issues = censor.check("他研究了制毒配方，还贩毒。")
        lines = "\n".join(format_issues(issues))
        assert "🔴 硬红线" in lines
        # 同一类别的「合规改法」只印一次，避免同一段建议刷屏
        assert lines.count("合规改法：") <= len({i["category"] for i in issues})

    def test_format_empty(self):
        from app.censor import format_issues

        assert format_issues([]) == []

    def test_summarize_ranks_by_count(self):
        from app.censor import summarize

        issues = censor.check("制毒。制毒。贩毒。求收藏。")
        out = summarize(issues)
        assert out and "问题类别汇总" in out[0]

    def test_verdict_variants(self):
        from app.censor import verdict

        assert "❌" in verdict(95.0, 1)
        assert "✅" in verdict(95.0, 0)
        assert "⚠️" in verdict(70.0, 0)
