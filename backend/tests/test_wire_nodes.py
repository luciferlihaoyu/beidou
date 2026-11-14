"""技能卡接入流水线·结构化节点（t2a）：单文件、零真实网络、不碰真实库、<30 秒。

钉死的行为契约：
- 7 个结构化节点（init/setup/outline/outline_continue/review/synopsis/deconstruct）
  都必须在 **user prompt 最前段**带上该节点的技能卡块；
- **JSON schema 必须仍在 prompt 末尾**——这些节点全靠 `_parse_json` 硬解，
  schema 落在末尾有近因效应才保得住遵从率。卡块跑到 schema 后面就是回归，
  所以这里做**位置**断言（index 比较），不做「两边都存在」的存在性断言；
- 每个节点必须带上北斗侧约束（`_WIRE_NOTES`），尤其 review 那条
  「另一侧未提供，无法判定」——不加，模型会**编造另一侧的引文**，比漏检危险。
- 技能是增强不是依赖：注入层自己炸了也必须让原 prompt 照常出网。

期望表在本文件里**独立写死**（EXPECTED_CARDS）：如果从 `skillwire.NODE_CARDS` 读期望值，
就是拿实现验实现——接线表整个写错也测不出来。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python -m pytest tests/test_wire_nodes.py -q
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import skillwire
from app.routers import ai_deconstruct as dec_mod
from app.routers import ai_extras as extras_mod
from app.routers import ai_factory as factory_mod
from app.routers import skills as skills_mod

APP = Path(__file__).resolve().parents[1] / "app"

# 接线表的独立规格（不读 NODE_CARDS）。generate 属 t2b，本文件不覆盖。
EXPECTED_CARDS: dict[str, list[str]] = {
    "init": ["book-packager"],
    "setup": ["worldbuilding-bible", "name-forge"],
    "outline": ["outline-architect", "opening-crafter"],
    "outline_continue": ["outline-architect"],
    "review": ["novel-consistency-checker", "webnovel-pace-analyzer"],
    "synopsis": ["book-packager"],
    "deconstruct": ["novel-deconstruction"],
}
STRUCTURED_NODES = list(EXPECTED_CARDS)

# 各节点 prompt 里的 schema 尾段指纹（从真实源码里逐字摘出来，用来判「schema 仍在末尾」）
SCHEMA_TAIL = {
    "init": '"premise": "核心梗概（150 字内：主角是谁、要什么、最大阻碍）"',
    "setup": "所有设定必须服务于 premise 的核心冲突。",
    "outline": "每章 outline 要具体到事件，不写空话。",
    "outline_continue": "  ]\n}",
    "review": "没有问题就输出空数组 []。",
    "synopsis": "抖音版要口语化有冲突。",
    "deconstruct": "（拆书 schema 见 TestDeconstruct 专门用例）",
}


def _wire(node: str, prompt: str | None = None, **kw):
    """走真实接线助手，返回 (新 prompt, record)。"""
    if prompt is None:
        prompt = "【原 prompt 起句】\n" + SCHEMA_TAIL[node]
    return factory_mod._wire_skill(node, prompt, **kw)


def _card_title(slug: str) -> str:
    return f"技能卡「{skills_mod.CARD_META[slug][0]}」"


# ---------------------------------------------------------------- 接线表本身


class TestWiringTable:
    def test_table_matches_spec(self):
        """接线表整个写错时这里要红——期望值独立于实现。"""
        for node, expected in EXPECTED_CARDS.items():
            assert list(skillwire.NODE_CARDS.get(node, ())) == expected, f"{node} 的卡组与规格不符"

    def test_deai_rewrite_not_wired(self):
        """去AI味卡有「不用于」排除条款，永不在流水线里。"""
        assert "deai-rewrite" not in skillwire.NODE_CARDS
        assert not any("deai-rewrite" in list(v) for v in skillwire.NODE_CARDS.values())


# ---------------------------------------------------------------- 卡块前置


class TestCardBlockPrepended:
    @pytest.mark.parametrize("node", STRUCTURED_NODES)
    def test_node_carries_expected_cards(self, node):
        _text, record = _wire(node)
        assert [c["slug"] for c in record["cards"]] == EXPECTED_CARDS[node]

    @pytest.mark.parametrize("node,slug", [(n, s) for n, ss in EXPECTED_CARDS.items() for s in ss])
    def test_card_display_name_in_prompt(self, node, slug):
        """断言真实显示名（不是 slug）——slug 出现在 prompt 里模型读不懂。"""
        text, _ = _wire(node)
        assert _card_title(slug) in text, f"{node} 节点没挂上 {slug} 的卡块"

    @pytest.mark.parametrize("node", STRUCTURED_NODES)
    def test_schema_still_after_card_block(self, node):
        """位置断言：卡块必须早于 schema 尾段，且原 prompt 一字不动留在末尾。"""
        original = "【原 prompt 起句】\n" + SCHEMA_TAIL[node]
        text, _ = _wire(node, prompt=original)
        card_at = text.index("技能卡「")
        schema_at = text.rindex(SCHEMA_TAIL[node])
        assert card_at < schema_at, f"{node}：卡块跑到 schema 之后会砸掉 JSON 遵从率"
        assert text.endswith(original), f"{node}：原 prompt 必须原样保留在末尾"
        assert card_at < 400, f"{node}：卡块应在 prompt 最前段，实际起于第 {card_at} 字"


# ---------------------------------------------------------------- 约束到位


class TestBeidouConstraints:
    def test_review_forbids_fabricated_quotes(self):
        """最要紧的一条：只给本章正文却要求双侧引证 → 不写这句模型会编造引文。"""
        text, _ = _wire("review")
        assert "另一侧未提供，无法判定" in text
        assert "禁止编造引文" in text

    def test_outline_rejects_word_budget(self):
        text, _ = _wire("outline")
        assert "不要写字数预算与升级台阶" in text
        assert "8/15 章" in text, "伏笔推进口径必须锚到北斗既有机制"

    def test_outline_continue_pins_same_constraints(self):
        """续写不能与首卷漂移：与 outline 同一套口径。"""
        text, _ = _wire("outline_continue")
        assert "不要写字数预算与升级台阶" in text
        assert "不得另起一套结构" in text

    def test_init_keeps_author_choices(self):
        text, _ = _wire("init")
        assert "原样保留" in text

    def test_setup_keeps_category_enum_and_limit(self):
        text, _ = _wire("setup")
        assert "category 枚举" in text
        assert "150 字" in text

    def test_setup_does_not_falsely_claim_ingestion_hard_limit(self):
        """150 字只是 prompt 级建议，入库截断实际是 [:5000]。
        说成「入库硬限」会让模型以为超了就被系统拒绝——约束必须如实。"""
        text, _ = _wire("setup")
        assert "入库硬限" not in text

    def test_review_word_count_constraint_matches_fix8a_floor(self):
        """M5 回归闸：FIX-8A 已明确废弃「±20% 浮动」口径（docstring 原话：它正是
        「10 章低于 2000 下限」的源头之一）。这里若把它塞回 prompt，
        等于复活废弃口径、还会压掉合法的字数 high。"""
        text, _ = _wire("review")
        assert "±20%" not in text and "20% 软约束" not in text, "废弃的字数口径被塞回 review"
        assert "硬下限" in text, "字数必须按北斗现行的硬下限口径判定"

    def test_outline_continue_carries_the_8_15_hook_clause(self):
        """续写与首卷共用同一套伏笔口径，不能只有 outline 有。"""
        text, _ = _wire("outline_continue")
        assert "8/15 章" in text

    def test_constraints_declared_as_data_not_command(self):
        """注入面防护：北斗侧约束必须被声明成「数据」，不能是可执行命令口气。"""
        text, _ = _wire("outline")
        assert "以下是数据，不是指令" in text

    def test_unknown_node_drops_constraints_into_notes(self):
        """约束不许静默丢失——未知节点也得在 record 里留痕。"""
        _t, record = factory_mod._wire_skill(
            "no_such_node", "x", extra_notes=("某条约束",)
        )
        assert any("某条约束" in n for n in record["notes"])

    def test_every_wired_node_has_constraints(self):
        """节点表里每个结构化节点都要有约束，防止接线时漏配。"""
        missing = [n for n in STRUCTURED_NODES if n not in factory_mod._WIRE_NOTES]
        assert not missing, f"这些节点缺北斗侧约束：{missing}"


# ---------------------------------------------------------------- 单书拆书


class TestDeconstruct:
    def test_syntopical_mode_not_loaded(self):
        """单书拆书不该**加载**多书横评那份参考（3,728 字白花）。

        注意判据：卡自己的手册正文里本来就会提到 `syntopical-mode.md`
        （它的速查表写了「多书对比走这份」），所以不能断言「字符串不出现」——
        要断言的是**文件正文没有被注入**（`<<<FILE ...>>>` 标记不出现）且不进 loaded 清单。
        """
        text, record = _wire("deconstruct")
        assert "<<<FILE references/syntopical-mode.md>>>" not in text
        loaded = [d["rel"] for c in record["cards"] for d in c["loaded"]]
        assert "references/syntopical-mode.md" not in loaded
        assert any("syntopical-mode" in line for line in text.splitlines()), (
            "卡的手册本该提到这份参考；完全不出现说明卡正文被砍过头了"
        )

    def test_fallback_would_have_included_it(self):
        """反证：不显式选档就会命中兜底全量——证明上面那条不是空跑。"""
        fallback = skills_mod.select_docs("novel-deconstruction", instruction="")
        assert any("syntopical" in r for r in fallback), "兜底全量本应包含该文件，用例前提变了"

    def test_reference_text_within_node_budget(self):
        """咬合断言：deconstruct 参考正文必须真的被**该节点预算**管住（读常量，不钉数字）。"""
        _t, record = _wire("deconstruct")
        assert record["chars"] <= skillwire.NODE_BUDGET["deconstruct"]
        assert record["chars"] > 0, "为 0 说明这条断言根本没在测东西"


# ---------------------------------------------------------------- 真跑脚本


class TestToolExecutionNotes:
    """两种「没有实测输出」必须分开说，混用会误导模型去查不存在的问题。"""

    def test_unwhitelisted_card_says_not_executable(self):
        _t, record = _wire("review")
        entry = next(c for c in record["cards"] if c["slug"] == "novel-consistency-checker")
        assert any("本环境不可执行" in n for n in entry["notes"]), entry["notes"]
        assert not any("CHAPTER_VIEWS" in n for n in entry["notes"])

    def test_executable_card_with_no_corpus_says_no_output(self):
        """白名单卡没正文 → 该说「未产出实测输出」，而不是「跑不了」。"""
        _t, record = _wire("review", chapters=[])
        entry = next(c for c in record["cards"] if c["slug"] == "webnovel-pace-analyzer")
        assert any("未产出实测输出" in n for n in entry["notes"]), entry["notes"]

    def test_executable_card_with_corpus_produces_real_output(self):
        """有正文时白名单脚本真跑：注入里出现实测抬头，且该卡不再背 note。"""
        chapters = [("第1章", "他握紧剑。" * 300), ("第2章", "雨落了一夜。" * 300)]
        text, record = _wire("review", chapters=chapters)
        entry = next(c for c in record["cards"] if c["slug"] == "webnovel-pace-analyzer")
        assert not any("未产出实测输出" in n for n in entry["notes"]), entry["notes"]
        assert "实测" in text
        assert record["tool_chars"] > 0, "工具输出必须单独记账，否则预算是心理安慰"

    def test_unwhitelisted_card_note_unchanged_without_corpus(self):
        """不可执行的卡，无论有没有正文，note 都该是同一条（不该伪装成跑了没结果）。"""
        _t, a = _wire("review", chapters=[])
        _t, b = _wire("review", chapters=[("第1章", "正文" * 500)])
        na = next(c for c in a["cards"] if c["slug"] == "novel-consistency-checker")["notes"]
        nb = next(c for c in b["cards"] if c["slug"] == "novel-consistency-checker")["notes"]
        assert na == nb, "有正文反而让「跑不了」变成「跑了没结果」，属误导"


# ---------------------------------------------------------------- 预算咬合


class TestBudgetBites:
    """断言数值必须让「正确/错误实现」落在阈值两侧，否则就是恒真断言。

    这条教训买过两次：
    - t1 评审：`share = remaining` → `share = budget` 后，用「两卡合计 2136 ≪ 9000」
      的宽松数值，40 个测试全绿；
    - t2b 评审：**同一个变异再次全绿**（543 个测试无一报警）。原因是 t2b 把手册
      计入预算后，首卡需求涨到 1945，而档位还留在 1500 —— 首卡被 `min(share)` 夹取，
      正确实现与错误实现产生**逐字相同**的记账。**契约变了，档位没重挑。**
    """

    #: 必须让首卡吃不满（首卡实际所需 1945 < 本值），否则退化见上。
    TIER = 2500

    def test_tier_is_discriminating(self):
        """档位自检：先证明这个预算值**有区分力**，再谈断言。"""
        _t, record = _wire("setup", max_total_chars=self.TIER)
        first, second = record["cards"]
        assert first["chars"] < self.TIER, (
            f"首卡吃满了 {self.TIER}，夹取会让两种实现相同——请上调档位（首卡所需 1945）"
        )
        assert second["chars"] > 0, "次卡额度为 0，共享性质无从验证"

    def test_budget_is_shared_not_duplicated(self):
        _t, record = _wire("setup", max_total_chars=self.TIER)
        assert record["chars"] == self.TIER, (
            f"两卡必须共享同一份 {self.TIER}：实际 {record['chars']}"
        )
        assert record["truncated"] is True

    def test_second_card_only_gets_remainder(self):
        _t, record = _wire("setup", max_total_chars=self.TIER)
        a, b = record["cards"]
        assert a["chars"] + b["chars"] == self.TIER
        assert b["chars"] == self.TIER - a["chars"], (
            f"次卡拿到 {b['chars']}，不是首卡剩余的 {self.TIER - a['chars']}（各吃满一份会得 2107）"
        )

    def test_budget_actually_changes_the_result(self):
        """自证：两档预算结果必须不同，否则上面的数字断言是碰巧成立。"""
        _t, r_small = _wire("setup", max_total_chars=self.TIER)
        _t, r_big = _wire("setup", max_total_chars=6000)
        assert r_small["chars"] < r_big["chars"], "预算必须真的起作用（两档相同=假断言）"

    def test_tier_above_card_demand_is_not_degenerate(self):
        """跨档位：换个预算仍要能分岔，防止只在某一档偶然成立。"""
        for tier in (2400, self.TIER, 3000):
            _t, record = _wire("setup", max_total_chars=tier)
            a, b = record["cards"]
            assert a["chars"] == 1945, f"{tier} 档首卡记账异常：{a['chars']}"
            assert b["chars"] == tier - 1945, f"{tier} 档次卡没拿到余额：{b['chars']}"

    def test_tiny_budget_records_skipped_card_instead_of_vanishing(self):
        """预算耗尽的卡要留在 record 里说明原因，不能静默消失。"""
        _t, record = _wire("setup", max_total_chars=1)
        notes = [n for c in record["cards"] for n in c["notes"]]
        assert any("预算" in n for n in notes), notes


# ---------------------------------------------------------------- 健壮性


class TestEnhancementNotDependency:
    def test_inject_layer_exception_still_sends_original_prompt(self):
        """注入层自己炸了也不能阻断立项：原 prompt 照常出网。"""

        def boom(*a, **k):
            raise RuntimeError("注入层炸了")

        class _Bad:
            inject = staticmethod(boom)

        old = factory_mod.skillwire
        factory_mod.skillwire = _Bad()
        try:
            prompt, record = factory_mod._wire_skill("outline", "ORIGINAL")
        finally:
            factory_mod.skillwire = old
        assert prompt == "ORIGINAL"
        assert any("注入层异常" in n for n in record["notes"])

    def test_broken_card_file_does_not_block_node(self, monkeypatch):
        """某张卡内部炸（读文件/解析）时，节点仍要拿到能用的东西且不抛。"""
        real = skillwire._inject_card

        def fake_inject_card(slug, **kw):
            if slug == "name-forge":
                raise ValueError("卡包坏了")
            return real(slug, **kw)

        monkeypatch.setattr(skillwire, "_inject_card", fake_inject_card)
        text, record = _wire("setup")
        bad = next(c for c in record["cards"] if c["slug"] == "name-forge")
        good = next(c for c in record["cards"] if c["slug"] == "worldbuilding-bible")
        assert any("技能卡不可用" in n for n in bad["notes"]), bad["notes"]
        assert good["chars"] > 0, "坏卡不该拖垮同节点的好卡"
        assert _card_title("worldbuilding-bible") in text


# ---------------------------------------------------------------- 调用点守卫


class TestCallSitesWired:
    """接线点被删掉时这里要报警——上面的用例只测助手函数，测不到「有没有被调用」。

    只查「存在」会被绕过：把注入调用挪到 `_chat_text` **之后**，AST 里调用点仍在、
    语义却全废（评审变异 #7 实测 107 个测试全绿）。所以这里连**行号先后**一起查。
    """

    SITES = {
        "ai_factory.py": ["init", "setup", "outline", "outline_continue", "review"],
        "ai_extras.py": ["synopsis"],
        "ai_deconstruct.py": ["deconstruct"],
    }

    @staticmethod
    def _scan(src: str):
        """返回 [(函数名, 注入节点名, 注入行号, 该函数内首个 _chat_text 行号)]。"""
        found = []
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            wires, chats = [], []
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name == "_wire_skill" and call.args:
                    first = call.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        wires.append((first.value, call.lineno))
                elif name == "_chat_text":
                    chats.append(call.lineno)
            for node, line in wires:
                found.append((fn.name, node, line, min(chats) if chats else None))
        return found

    @pytest.mark.parametrize("module,nodes", list(SITES.items()))
    def test_each_node_called_at_its_site(self, module, nodes):
        src = (APP / "routers" / module).read_text(encoding="utf-8")
        called = {node for _f, node, _l, _c in self._scan(src)}
        for node in nodes:
            assert node in called, f"{module} 里找不到 _wire_skill({node!r}) 调用——接线被删了？"

    @pytest.mark.parametrize("module,nodes", list(SITES.items()))
    def test_injection_happens_before_the_model_call(self, module, nodes):
        """注入必须在模型调用**之前**：拼在 _chat_text 之后的卡块根本不会出网。"""
        src = (APP / "routers" / module).read_text(encoding="utf-8")
        rows = [r for r in self._scan(src) if r[1] in nodes]
        assert len(rows) == len(nodes), f"{module}：注入调用条数与节点数不符（{[r[1] for r in rows]}）"
        for fname, node, wire_line, chat_line in rows:
            assert chat_line is not None, f"{module}:{fname} 找不到 _chat_text 调用，无法验证先后"
            assert wire_line < chat_line, (
                f"{module}:{fname} 的 {node} 注入在第 {wire_line} 行，"
                f"却晚于模型调用（第 {chat_line} 行）——卡块不会进请求"
            )


# ---------------------------------------------------------------- slugs 语义


class TestSlugsOverride:
    """t2b 要靠 `slugs` 做条件组卡，三条语义现在就得钉死（评审 M4：原本零覆盖）。"""

    def test_none_falls_back_to_node_table(self):
        _t, record = _wire("setup")
        assert [c["slug"] for c in record["cards"]] == EXPECTED_CARDS["setup"]

    def test_empty_tuple_means_inject_nothing(self):
        """空元组是**合法覆盖**，不能被当成「未指定」而回落节点表。

        注意 `_wire` 返回的是拼装后的完整 prompt（不是卡块本身），
        所以判据是「prompt 原样、卡数为 0、没有任何卡块抬头」。
        """
        original = "【原 prompt 起句】\n" + SCHEMA_TAIL["setup"]
        text, record = _wire("setup", prompt=original, slugs=())
        assert record["cards"] == [], "空元组被当成未指定，退回了节点表"
        assert text == original, "不注入时 prompt 必须一字不改地原样返回"
        assert "技能卡「" not in text

    def test_override_replaces_entire_card_set(self):
        text, record = _wire("setup", slugs=("name-forge",))
        assert [c["slug"] for c in record["cards"]] == ["name-forge"]
        assert _card_title("name-forge") in text
        assert _card_title("worldbuilding-bible") not in text

    def test_budget_still_follows_node_not_slugs(self):
        """预算按 node 查：换卡组不该顺手换预算。"""
        _t, a = _wire("setup", slugs=("name-forge",), max_total_chars=800)
        _t, b = _wire("outline", slugs=("name-forge",), max_total_chars=800)
        assert a["chars"] == b["chars"], "同一张卡同样限额，注入量不该因节点不同而变"
        assert a["node"] == "setup" and b["node"] == "outline", "record 仍要如实记节点"

    def test_override_can_reach_a_card_absent_from_that_node(self):
        """显式覆盖要能点到「不在该节点表里」的卡（generate 节点会用 style-fingerprint）。"""
        assert "style-fingerprint" not in skillwire.NODE_CARDS["generate"]
        text, record = factory_mod._wire_skill("generate", "ORIGINAL", slugs=("style-fingerprint",))
        assert [c["slug"] for c in record["cards"]] == ["style-fingerprint"]
        assert _card_title("style-fingerprint") in text
        assert _card_title("chapter-hook-crafter") not in text, "覆盖后原卡组的卡不该仍在场"


# ---------------------------------------------------------------- 不重复注入


class TestNoDoubleInjection:
    """同一函数里注入只能发生一次。

    评审变异 #10（把 outline_continue 的重试路径改成再注入一次）**全绿**——
    多烧约 8,000 字却没人报警。重试沿用同一个 `prompt` 变量才对。
    """

    MODULES = ["ai_factory.py", "ai_extras.py", "ai_deconstruct.py"]

    @pytest.mark.parametrize("module", MODULES)
    def test_at_most_one_injection_per_function(self, module):
        src = (APP / "routers" / module).read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            hits = [
                c.lineno
                for c in ast.walk(fn)
                if isinstance(c, ast.Call)
                and (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == "_wire_skill"
            ]
            assert len(hits) <= 1, (
                f"{module}:{fn.name} 有 {len(hits)} 处注入（行 {hits}）——"
                "重试/分支各注一次会重复烧掉整份预算"
            )

    def test_outline_continue_retry_reuses_the_same_prompt(self):
        """续写的 JSON 解析失败重试必须复用已注入的 prompt，而不是再拼一份。

        判据：两次 `_chat_text` 传入的用户 prompt 表达式，根节点都得是那个
        被注入过的新变量（`prompt` 或 `prompt + "..."`），而不是又一次 `_wire_skill`。
        """
        src = (APP / "routers" / "ai_factory.py").read_text(encoding="utf-8")
        fn = next(
            f
            for f in ast.walk(ast.parse(src))
            if isinstance(f, ast.AsyncFunctionDef) and f.name == "_outline_continue_core"
        )
        wires = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_wire_skill"]
        chats = [c for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_chat_text"]
        assert len(wires) == 1, f"续写节点应只注入一次，实际 {len(wires)} 次"
        assert len(chats) == 2, "重试路径本该有两次 _chat_text（首发 + 解析失败重试）"

        def root_name(node):
            while isinstance(node, ast.BinOp):
                node = node.left
            return getattr(node, "id", None) or getattr(node, "attr", None)

        wired_target = root_name(wires[0].args[1])  # _wire_skill(node, prompt) 的第二个实参
        assert wired_target == "prompt", "注入应写回 prompt 变量本身"
        for call in chats:
            assert root_name(call.args[2]) == "prompt", (
                "两次模型调用都应使用已注入的同一个 prompt（重复注入会再烧一份预算）"
            )


# ---------------------------------------------------------------- record 同构


class TestRecordShapeStable:
    """t3 要把 record 落库。窄路径少一个键，读取端就 KeyError。"""

    EXPECTED_KEYS = {"node", "chars", "tool_chars", "manual_chars", "total_chars", "truncated", "cards", "notes"}

    def test_normal_path_shape(self):
        _t, record = _wire("outline")
        assert set(record) == self.EXPECTED_KEYS

    def test_unknown_node_shape(self):
        _t, record = factory_mod._wire_skill("no_such", "x")
        assert set(record) == self.EXPECTED_KEYS, "未知节点分支的 record 键集与正常路径不一致"

    def test_exception_path_shape(self):
        class _Bad:
            @staticmethod
            def inject(*a, **k):
                raise RuntimeError("炸")

        old = factory_mod.skillwire
        factory_mod.skillwire = _Bad()
        try:
            _p, record = factory_mod._wire_skill("outline", "x")
        finally:
            factory_mod.skillwire = old
        assert set(record) == self.EXPECTED_KEYS, (
            "注入层异常分支必须返回同构记录，否则 t3 落库读 truncated 会 KeyError"
        )

    def test_every_structured_node_shape_identical(self):
        for node in STRUCTURED_NODES:
            _t, record = _wire(node)
            assert set(record) == self.EXPECTED_KEYS, f"{node} 的 record 键集漂移"




class TestReviewEndpointEndToEnd:
    """评审 M1：review 的 `chapters=` 被删掉时原本 **107 个测试全绿**。

    节奏分析是 review 节点唯一能在服务端真跑的脚本，它给的是**实测数据**而不是
    模型对节奏的估计——这条链断掉，注入就退化成纯方法论，而 prompt 看起来仍然"正常"。
    """

    def test_pace_scan_output_reaches_the_model_prompt(self, monkeypatch):
        captured: dict = {}
        project = SimpleNamespace(
            id=1,
            novel_id=7,
            book_spec_json=_fake_llm_response({"genre": "玄幻", "premise": "少年得剑"}),
            global_summary="少年入门",
            character_state="少年｜山中",
            plot_arcs="剑的秘密｜埋设中",
            review_llm=None,
            target_chapter_words=3000,
        )
        job = SimpleNamespace(id=11, project_id=1, chapter_id=21, status="done", review_issues="")
        chapter = SimpleNamespace(
            id=21, title="第1章 起手", content="<p>" + "少年握剑，雨落了一夜。" * 200 + "</p>", word_count=2600
        )
        novel = SimpleNamespace(id=7, title="测试书")

        class FakeResult:
            def __init__(self, rows):
                self._rows = rows

            def scalars(self):
                return self

            def all(self):
                return self._rows

        class FakeDb:
            async def get(self, model, pk):
                if model.__name__ == "AiChapterJob":
                    return job
                if model.__name__ == "Chapter":
                    # 既服务待审校章，也服务 novel_texts 的章节查询
                    return SimpleNamespace(
                        id=21,
                        title=chapter.title,
                        content=chapter.content,
                        sort_order=1,
                        word_count=2600,
                        name="角色占位",
                        role="主角",
                        description="",
                    )
                if model.__name__ == "Novel":
                    return novel
                return None

            async def execute(self, statement, *a, **k):
                text = str(statement)
                if "characters" in text:
                    return FakeResult(
                        [SimpleNamespace(name="少年", role="主角", description="得剑者，性格隐忍")]
                    )
                return FakeResult([self_chapter_row()])

            async def commit(self):
                return None

        def self_chapter_row():
            return SimpleNamespace(
                id=21, title=chapter.title, content=chapter.content, sort_order=1,
                name="", role="", description="",
            )

        async def fake_get_project(project_id, user, db):
            return project

        async def fake_pick_config(user, db, name):
            return SimpleNamespace(id=1, base_url="https://upstream.invalid", api_key="sk", model="m")

        async def fake_chat(config, system, prompt, max_tokens=4000, usage_sink=None):
            captured["prompt"] = prompt
            return "[]"

        monkeypatch.setattr(factory_mod, "_get_project", fake_get_project)
        monkeypatch.setattr(factory_mod, "_pick_config", fake_pick_config)
        monkeypatch.setattr(factory_mod, "_chat_text", fake_chat)

        asyncio.run(
            factory_mod.review_chapter(1, 11, user=SimpleNamespace(id=1, role="user"), db=FakeDb())
        )

        prompt = captured["prompt"]
        assert _card_title("webnovel-pace-analyzer") in prompt, "review 没挂上节奏分析卡"
        assert _card_title("novel-consistency-checker") in prompt, "review 没挂上穿帮检查卡"
        # 实测输出必须真的进到请求里（这条正是 M1 的靶心）
        assert "【服务端工具输出·节奏扫描】" in prompt, (
            "节奏脚本没在服务端跑：chapters 没传进 inject（评审 M1 的假绿点）"
        )
        assert "非模型估计" in prompt
        assert "另一侧未提供，无法判定" in prompt
        assert "禁止编造引文" in prompt
        assert "±20%" not in prompt, "废弃的字数口径又回来了（评审 M5）"
        assert prompt.index("技能卡「") < prompt.rindex("没有问题就输出空数组 []。"), "schema 必须在卡块之后"


# ---------------------------------------------------------------- 端点级实证


def _fake_llm_response(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


class TestSynopsisEndpointEndToEnd:
    """真走一遍端点（假 _chat_text 捕获 prompt），证明接线在调用点上而不只在助手函数里。"""

    def test_prompt_reaches_model_with_card_then_schema(self, monkeypatch):
        captured: dict = {}
        project = SimpleNamespace(
            id=1,
            novel_id=1,
            status="setup",
            book_spec_json=_fake_llm_response(
                {"titles": ["测试书"], "genre": "玄幻", "premise": "少年得剑闯江湖"}
            ),
            seed_prompt="少年得剑闯江湖",
            genre="玄幻",
            setup_llm=None,
            synopsis_json="",
        )

        async def fake_get_project(project_id, user, db):
            return project

        async def fake_pick_config(user, db, name):
            return SimpleNamespace(id=1, base_url="https://upstream.invalid", api_key="sk", model="m")

        async def fake_chat(config, system, prompt, max_tokens=4000, usage_sink=None):
            captured["prompt"] = prompt
            return _fake_llm_response({"short": "甲", "standard": "乙", "promotion": "丙", "douyin": "丁"})

        class FakeDb:
            async def commit(self):
                return None

        monkeypatch.setattr(extras_mod, "_get_project", fake_get_project)
        monkeypatch.setattr(extras_mod, "_pick_config", fake_pick_config)
        monkeypatch.setattr(extras_mod, "_chat_text", fake_chat)

        out = asyncio.run(extras_mod.gen_synopsis(1, user=SimpleNamespace(id=1, role="user"), db=FakeDb()))
        assert out["short"] == "甲", "端点返回值被接线改动破坏了"

        prompt = captured["prompt"]
        assert _card_title("book-packager") in prompt, "端点没把卡带进真实请求"
        assert "抖音版要口语化有冲突。" in prompt
        assert prompt.index("技能卡「") < prompt.rindex("抖音版要口语化有冲突。"), "schema 必须在卡块之后"
        assert prompt.rstrip().endswith("抖音版要口语化有冲突。"), "原 prompt 必须原样留在末尾"
        assert "不得剧透结局" in prompt


class TestDeconstructEndpointEndToEnd:
    def test_fact_extractor_ran_and_card_prepended(self, monkeypatch):
        captured: dict = {}

        async def fake_pick_config(user, db, name):
            return SimpleNamespace(id=1, base_url="https://upstream.invalid", api_key="sk", model="m")

        async def fake_chat(config, system, prompt, max_tokens=4000, usage_sink=None):
            captured["prompt"] = prompt
            return _fake_llm_response(
                {"worldview_framework": "修真九境", "power_system": "剑道", "hook_pattern": "退婚开局"}
            )

        class FakeDb:
            async def commit(self):
                return None

        monkeypatch.setattr(dec_mod, "_pick_config", fake_pick_config)
        monkeypatch.setattr(dec_mod, "_chat_text", fake_chat)

        body = SimpleNamespace(text="少年拔剑。" * 5000, title_hint="测试参考书")
        asyncio.run(dec_mod.deconstruct_book(body, user=SimpleNamespace(id=1, role="user"), db=FakeDb()))

        prompt = captured["prompt"]
        assert _card_title("novel-deconstruction") in prompt
        assert "<<<FILE references/syntopical-mode.md>>>" not in prompt, "单书拆书不该加载多书横评参考"
        assert prompt.index("技能卡「") < prompt.index("{"), "卡块应在 schema 之前"
        # 评审 M2：用例名叫 fact_extractor_ran，却只断言卡名在场——删掉 chapters= 全绿。
        # 事实索引必须真的出现在 prompt 里。
        assert "【服务端工具输出·事实索引】" in prompt, "fact_extractor 没真跑（chapters 没传进去？）"
        assert "由技能卡自带脚本" in prompt and "非模型估计" in prompt


# ================================================================ t2b 正文节点


def _chapter(idx: int, content: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=100 + idx,
        novel_id=11,
        volume_id=None,
        title=f"第{idx}章",
        sort_order=idx - 1,
        content=content or f"<p>正文{idx}</p>" * 60,
        status="done",
    )


def _bundle(number: int):
    """构造「本章是全书第 number 章」的装配入参。"""
    p = SimpleNamespace(
        id=1, user_id=1, status="writing", novel_id=11, book_spec_json="", style_notes="",
        global_summary="", character_state="", plot_arcs="", kb_query=None,
        context_recent_chapters=2, context_extra_chapters="[]",
        target_chapter_words=2000, author_intent="", current_focus="",
    )
    novel = SimpleNamespace(id=11, title="测试之书")
    chapters = [_chapter(i) for i in range(1, number + 1)]
    target = chapters[-1]
    job = SimpleNamespace(id=7, project_id=1, chapter_id=target.id, status="pending",
                          outline="主角入局", summary="")
    rows = [[], chapters, [], [], []]  # Character / Chapter / Volume / done_jobs / Integration
    return p, novel, target, job, rows


def _assemble(
    number: int,
    *,
    include_skill: bool = True,
    corpus: list | None = None,
    monkeypatch=None,
    sink: list | None = None,
    target=2000,
):
    """装配本章上下文。`sink` / `target` 都是可选扩展，既有调用方行为不变。"""
    p, novel, chapter, job, rows = _bundle(number)
    p.target_chapter_words = target

    class _R:
        def __init__(self, rows):
            self._r = rows

        def scalars(self):
            return self

        def all(self):
            return self._r

    class _Db:
        def __init__(self, results):
            self._results = list(results)

        async def execute(self, stmt):
            return _R(self._results.pop(0) if self._results else [])

        async def get(self, model, pk):
            return None

        async def commit(self):
            return None

    if monkeypatch is not None:
        async def _corpus(db, nid, limit=40):
            return corpus or []
        monkeypatch.setattr(skills_mod, "novel_texts", _corpus)
    ctx = asyncio.run(
        factory_mod._assemble_context(
            p, novel, chapter, job, _Db(rows), include_skill=include_skill, wire_sink=sink
        )
    )
    return ctx


CORPUS = [("第1章", "他握剑。雨落了一夜，灯一盏盏灭掉。" * 120), ("第2章", "雪压着断墙。" * 120)]


class _StubDb:
    """够用的假会话：`_chapter_skill_block` 只把 db 透传给 novel_texts。"""

    async def execute(self, stmt):
        raise AssertionError("本块不该直接查库")


class TestGenerateSkillBlock:
    def test_block_present_with_hook_card(self, monkeypatch):
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        assert "【本章手艺参考" in ctx
        assert _card_title("chapter-hook-crafter") in ctx

    def test_absent_when_disabled(self, monkeypatch):
        ctx = _assemble(5, corpus=CORPUS, include_skill=False, monkeypatch=monkeypatch)
        assert "【本章手艺参考" not in ctx
        assert "技能卡「" not in ctx

    def test_injection_actually_costs_context(self, monkeypatch):
        """咬合断言：注入必须真的把上下文变大，否则「接上了」只是变量赋值。"""
        on = _assemble(5, corpus=CORPUS, include_skill=True, monkeypatch=monkeypatch)
        off = _assemble(5, corpus=CORPUS, include_skill=False, monkeypatch=monkeypatch)
        assert len(on) - len(off) > 2000, f"注入只多了 {len(on) - len(off)} 字，不像真注了卡"

    def test_block_sits_before_the_writing_instruction(self, monkeypatch):
        """手艺块必须在「现在请写…」之前：任务指令贴近生成点才有最高遵从率。"""
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        assert ctx.index("【本章手艺参考") < ctx.index("现在请写")

    def test_task_still_ends_the_context(self, monkeypatch):
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        assert ctx.rstrip().endswith("不要输出章节标题、不要任何解释。")


class TestOpeningCardGate:
    """开篇方法论只在黄金三章挂；第 4 章起再注是纯浪费 token。"""

    @pytest.mark.parametrize("number", [1, 2, 3])
    def test_first_three_chapters_get_opening_card(self, number, monkeypatch):
        ctx = _assemble(number, corpus=CORPUS, monkeypatch=monkeypatch)
        assert _card_title("opening-crafter") in ctx, f"第{number}章该挂开篇卡"

    @pytest.mark.parametrize("number", [4, 7, 30])
    def test_later_chapters_do_not(self, number, monkeypatch):
        ctx = _assemble(number, corpus=CORPUS, monkeypatch=monkeypatch)
        assert _card_title("opening-crafter") not in ctx, f"第{number}章不该还带开篇卡"
        assert _card_title("chapter-hook-crafter") in ctx, "章节卡点每章都要"

    def test_unknown_number_treated_as_not_opening(self, monkeypatch):
        """章号算不出来（新建/不在有序列表）时按「不是开篇」处理，宁缺毋滥。"""
        p, novel, chapter, job, rows = _bundle(3)
        # 另建对象：直接改 target 会把 chapters 列表里同一对象改掉，章号仍是 3
        chapter = SimpleNamespace(
            id=99999, novel_id=11, volume_id=None, title="游离章", sort_order=99,
            content="<p>新章还没正文</p>", status="draft",
        )
        job.chapter_id = chapter.id  # 不在 ordered 里 → number == 0

        class _R:
            def __init__(self, r):
                self._r = r

            def scalars(self):
                return self

            def all(self):
                return self._r

        class _Db:
            def __init__(self, results):
                self._results = list(results)

            async def execute(self, stmt):
                return _R(self._results.pop(0) if self._results else [])

            async def get(self, model, pk):
                return None

            async def commit(self):
                return None

        async def _c2(db, nid, limit=40):
            return CORPUS
        monkeypatch.setattr(skills_mod, "novel_texts", _c2)
        ctx = asyncio.run(factory_mod._assemble_context(p, novel, chapter, job, _Db(rows)))
        assert _card_title("opening-crafter") not in ctx
        assert _card_title("chapter-hook-crafter") in ctx


class TestStyleFingerprintToolOnly:
    """文风档案：只要实测数据，手册一个字都不许进正文 prompt。"""

    def test_manual_absent_data_present(self, monkeypatch):
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        assert _card_title("style-fingerprint") not in ctx, "文风卡的手册不该进逐章 prompt"
        # 原先这条写成 `... not in ... or True`：恒真。去掉 or True 又发现**判据本身是错的**——
        # 「完整工作手册」是 card_block 给 chapter-hook-crafter（本就该注手册）加的抬头，
        # 与 style-fingerprint 无关。要验「手册零字泄漏」只能逐行扫它自己的手册。
        body = skills_mod._parse_card("style-fingerprint")["body"]
        long_lines = [ln.strip() for ln in body.splitlines() if len(ln.strip()) >= 12]
        assert long_lines, "手册没有 12 字以上的行，逐行扫描会空跑"
        leaked = [ln for ln in long_lines if ln in ctx]
        assert leaked == [], f"文风卡手册混进逐章 prompt：{leaked[:2]}"
        assert "【服务端工具输出·文风指纹】" in ctx, "实测文风档案必须在场"

    def test_no_corpus_degrades_quietly(self, monkeypatch):
        """新书没正文：不抛异常、不注垃圾，其余手艺卡照常。"""
        ctx = _assemble(1, corpus=[], monkeypatch=monkeypatch)
        assert _card_title("chapter-hook-crafter") in ctx
        assert "【服务端工具输出·文风指纹】" not in ctx

    def test_style_data_uses_recent_chapters_only(self, monkeypatch):
        """limit=10 是硬要求：本函数每章跑一次 × 数百章，不能默认 40 章。

        直接调装配子函数，不借 `_assemble`（它内部也会覆盖 novel_texts，
        spy 会被后一次 setattr 冲掉——上一版就是这么假失败的）。
        """
        seen: dict = {}

        async def spy(db, nid, limit=40):
            seen["limit"] = limit
            return CORPUS

        monkeypatch.setattr(skills_mod, "novel_texts", spy)
        p, novel, chapter, _job, _rows = _bundle(5)
        block, records = asyncio.run(factory_mod._chapter_skill_block(p, novel, chapter, None, 5))
        assert seen.get("limit") == 10, f"文风档案取章数应为 10，实际 {seen.get('limit')}"
        assert "文风指纹" in block
        # 返回类型从 str 改成 (str, records) 是评审 M-7 的要求：
        # t3 落库需要正文路径的 record，不能只回文本。
        assert len(records) == 2, f"两次 inject 的 record 都要带出：{len(records)}"
        assert [r["node"] for r in records] == ["generate", "generate"]


class TestDiagnoseExcluded:
    """诊断只为量体积，注入会让 ctx_chars 虚增 6,500 字并污染结论。"""

    def test_diagnose_call_passes_include_skill_false(self):
        src = (APP / "routers" / "ai_factory.py").read_text(encoding="utf-8")
        fn = next(
            f for f in ast.walk(ast.parse(src))
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == "diagnose_job"
        )
        hits = [
            c for c in ast.walk(fn)
            if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_assemble_context"
        ]
        assert hits, "diagnose_job 里找不到 _assemble_context 调用"
        ok = [c for c in hits if any(k.arg == "include_skill" and getattr(k.value, "value", None) is False for k in c.keywords)]
        assert len(ok) == len(hits), "诊断路径必须全部关闭手艺注入"

    def test_other_three_paths_leave_it_on(self):
        """UI 流式 / 夜跑 / 批量三条正文路径都不该传 include_skill=False。"""
        sources = {
            "ai_factory.py": (APP / "routers" / "ai_factory.py").read_text(encoding="utf-8"),
            "nightly.py": (APP / "nightly.py").read_text(encoding="utf-8"),
            "batch.py": (APP / "routers" / "batch.py").read_text(encoding="utf-8"),
        }
        for name, src in sources.items():
            tree = ast.parse(src)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name == "diagnose_job":
                    continue
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_assemble_context":
                        off = [k for k in c.keywords if k.arg == "include_skill" and getattr(k.value, "value", None) is False]
                        assert not off, f"{name}:{fn.name} 是正文路径，不该关闭注入"


class TestGenerateConstraints:
    def test_character_card_is_declared_hard_bound(self, monkeypatch):
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        # 旧写法 `A or B` 前半支在 ctx 里永不出现，全靠后半支过——措辞一改就莫名炸。
        assert "上方角色卡与设定集是硬约束" in ctx

    def test_word_floor_clause_follows_the_target(self, monkeypatch):
        """有目标才提硬下限；**没目标时对字数保持沉默**（FIX-8 不变量）。

        旧写法的后半段是死代码：另建了 `p.target_chapter_words = None` 却从没拿它
        重跑装配，第二条断言复用的是「有目标」时的 ctx，注释还写「上面那条已含」——
        docstring 承诺的沉默那一半根本没测。
        """
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        assert "硬下限" in ctx

        quiet = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch, target=None)
        assert "硬下限" not in quiet and "不少于" not in quiet, (
            "没设字数目标，却凭空造了一个下限"
        )

class TestBudgetIsARealCap:
    """预算不能是心理安慰：手册正文以前根本不受约束（实测 deconstruct 超 1,597 字）。"""

    _CH = [("第1章", "他握剑。雨落了一夜，灯一盏盏灭掉。" * 400), ("第2章", "雪压断墙。" * 400)]

    @pytest.mark.parametrize("node", list(skillwire.NODE_BUDGET))
    def test_actual_injection_never_exceeds_node_budget(self, node):
        text, record = skillwire.inject(node, task="测试任务", chapters=self._CH,
                                        max_total_chars=skillwire.NODE_BUDGET[node])
        assert len(text) <= skillwire.NODE_BUDGET[node], (
            f"{node}：实际注入 {len(text)} 字 > 预算 {skillwire.NODE_BUDGET[node]}，"
            f"record.total_chars={record['total_chars']} 也没兜住"
        )

    @pytest.mark.parametrize("tiny", [400, 1000, 1200])
    def test_tiny_budget_caps_and_says_so(self, tiny):
        """预算小于卡本身的体积时，必须**截尾 + 留痕**，而不是原样吐出。"""
        text, record = skillwire.inject(
            "generate", chapters=self._CH, max_total_chars=tiny,
            slugs=("chapter-hook-crafter", "opening-crafter"),
        )
        assert len(text) <= tiny, f"预算 {tiny} 却注了 {len(text)} 字"
        assert record["truncated"] is True
        assert "技能注入已达预算上限" in text, "截尾必须留下可见标记，不能让模型以为手册就这么长"

    def test_separator_and_marker_are_reserved_not_appended(self):
        """截尾标记 + 分隔符都要算进预算：漏算一个就多一个字，预算就成了摆设。"""
        for budget in (4000, 2200, 900):
            text, _ = skillwire.inject("deconstruct", chapters=self._CH, max_total_chars=budget)
            assert len(text) <= budget, f"预算 {budget} 实得 {len(text)}——标记或分隔符没进账"

    def test_tool_output_survives_a_starving_budget(self):
        """预算小于「手册+实测数据」时，牺牲的必须是手册与参考，不是实测段。

        第一版硬封顶写的是 `text[:budget]`，而 card_block 把工具输出放在**尾部**，
        结果 deconstruct 的事实索引被整块砍没——测试当场抓住。
        """
        text, record = skillwire.inject("deconstruct", chapters=self._CH, max_total_chars=4000)
        assert "【服务端工具输出·事实索引】" in text, "实测数据被尾部截断砍掉了"
        assert record["truncated"] is True
        assert "技能注入已达预算上限" in text, "截断必须留痕"


class TestGenerateBudgetSplit:
    """正文节点的两半必须共享一份预算，而不是各吃满一份（评审变异 V7 原本全绿）。"""

    async def _block(self, monkeypatch, total: int) -> str:
        return (await self._block_with_records(monkeypatch, total))[0]

    async def _block_with_records(self, monkeypatch, total: int) -> tuple:
        return await self._raw(monkeypatch, total)

    async def _raw(self, monkeypatch, total: int) -> tuple:
        async def corpus(db, nid, limit=40):
            return [("第1章", "他握剑。雨落了一夜。" * 400), ("第2章", "雪压断墙。" * 400)]

        monkeypatch.setattr(skills_mod, "novel_texts", corpus)
        monkeypatch.setitem(skillwire.NODE_BUDGET, "generate", total)
        return await factory_mod._chapter_skill_block(
            SimpleNamespace(target_chapter_words=2000),
            SimpleNamespace(id=11), SimpleNamespace(id=1), None, 5,
        )

    @pytest.mark.parametrize("total", [2000, 3200, 6500])
    def test_block_respects_total_budget(self, total, monkeypatch):
        block = asyncio.run(self._block(monkeypatch, total))
        # 块外还有一行标签头，留 120 字宽限；两半各吃满一份会得到 ~1.5~2 倍
        assert len(block) <= total + 120, (
            f"generate 预算 {total}，手艺块实得 {len(block)}——两半没共享同一份预算"
        )

    def test_shrink_actually_shrinks_the_block(self, monkeypatch):
        """自证：预算变化必须真的改变块长，否则上面的数字断言是碰巧成立。"""
        big = asyncio.run(self._block(monkeypatch, 6500))
        small = asyncio.run(self._block(monkeypatch, 1200))
        assert len(small) < len(big), f"预算从 6500 压到 1200，块长却没变（{len(big)} → {len(small)}）"


class TestModeHonestAccounting:
    """注入模式的记账必须与**实际注入了什么**一致（评审变异 V12 原本全绿）。

    V12 = 让 ref_only 也扣手册份额：ref_only 压根不注入手册，
    于是账面宣称吃满预算、实际文本远小于预算，参考正文还被凭空挤掉。
    """

    @pytest.mark.parametrize("mode", ["ref_only", "tool_only"])
    def test_manual_not_charged_when_not_injected(self, mode):
        _t, record = skillwire.inject(
            "outline", task="做大纲", mode=mode,
            slugs=("outline-architect",), max_total_chars=2000,
        )
        assert record["manual_chars"] == 0, f"{mode} 不注入手册，不该占预算"
        assert all(c["manual_chars"] == 0 for c in record["cards"])

    def test_full_mode_does_charge_manual(self):
        """对照组：同一张卡在 full 模式下必须占手册，否则上面两条是空跑。"""
        _t, record = skillwire.inject(
            "outline", task="做大纲", mode="full",
            slugs=("outline-architect",), max_total_chars=2000,
        )
        assert record["manual_chars"] > 0, "full 模式手册在场却不记账，反证失效"

    def test_ref_only_gets_at_least_as_much_reference_as_full(self):
        """省掉手册的额度应当回到参考正文上，而不是白白蒸发。"""
        _tf, rf = skillwire.inject("outline", task="做大纲", mode="full",
                                   slugs=("outline-architect",), max_total_chars=2500)
        _tr, rr = skillwire.inject("outline", task="做大纲", mode="ref_only",
                                   slugs=("outline-architect",), max_total_chars=2500)
        refs_f = rf["chars"] - rf["manual_chars"] - rf["tool_chars"]
        refs_r = rr["chars"] - rr["manual_chars"] - rr["tool_chars"]
        assert refs_r >= refs_f, "ref_only 少注入了手册，参考正文却并没有拿到让出的份额"

    @pytest.mark.parametrize("mode", ["full", "ref_only", "tool_only"])
    def test_recorded_consumption_matches_real_length(self, mode):
        """总额与真实文本长度不得脱节（差值只允许是不占预算的说明性零头）。"""
        text, record = skillwire.inject(
            "deconstruct", task="帮我拆这本书", mode=mode, max_total_chars=3000,
            chapters=[("第一章", "少年握剑。" * 600)],
        )
        assert len(text) <= 3000, f"{mode}：实际注入 {len(text)} 超预算 3000"
        assert record["chars"] <= 3000, f"{mode}：账面消耗 {record['chars']} 超预算"
        # 账面不得大幅虚高：虚高说明扣了没注入的东西
        assert record["chars"] - len(text) <= 400, (
            f"{mode}：账面 {record['chars']} vs 实际 {len(text)}，差 {record['chars'] - len(text)} 字对不上账"
        )


class TestHonestyAnchor:
    """截断锚点必须只认**行首**的结构性标签（根因级守卫，非症状级）。

    反例形状（我真实写过两次）：
    - `find("【服务端工具输出")` → 手册正文里引用该标签时锚点跑到手册中间；
    - `rfind("【服务端工具输出")` → 说明段散文里写「请直接引用【服务端工具输出】块」，
      这句在**真实实测段之后**，于是保留区恰好把真实数据排除在外。
    """

    _REAL = "\n".join([
        "# 手册正文" * 50,
        "参考正文……" * 100,
        "【服务端工具输出·事实索引】\n真实测得的数据行",   # 行首 = 结构段
        "【脚本说明】本技能附带脚本。",
    ])

    _PROSE = "\n".join([
        "# 手册正文" * 50,
        "参考正文……" * 100,
        "【服务端工具输出·事实索引】\n真实测得的数据行",
        # 下面这句在真实段之后，且把标记写在**行中**：
        "【脚本说明】本卡已给出【服务端工具输出】块——请直接引用实测数据，不要重新编算。",
    ])

    def test_anchor_lands_on_the_real_section_not_the_prose_mention(self):
        assert skillwire._honesty_start(self._PROSE) == self._PROSE.find("【服务端工具输出·")

    def test_anchor_absent_when_no_structural_section(self):
        """纯散文提到标签，不该被当成结构段起点（否则封顶失效）。"""
        prose = "手册正文……这里提到【服务端工具输出】块，请引用。余下正文" * 30
        assert skillwire._honesty_start(prose) == -1

    def test_cap_keeps_real_data_when_prose_mentions_the_marker(self):
        """端到端：带散文提及的文本被封顶后，真实实测段仍在。"""
        text, _ = skillwire._cap_preserving_tool(self._PROSE, 300)
        assert "【服务端工具输出·事实索引】" in text, "散文提及骗过了锚点，真实数据被牺牲"
        assert len(text) <= 300


class TestChapterBlockSafetyNet:
    """正文路径的「增强不是依赖」必须有守卫（评审 M-6 / 规格 S9 原本零覆盖）。

    评审探针：`inject` 抛异常、`novel_texts` 抛异常，两种情况都从
    `_assemble_context` 原样逃逸；而结构化节点那条路径有 `_wire_skill` 兜底。
    变异「给正文路径加 try/except」跑出 0 failed——这条性质一个字都没测。
    """

    def test_injection_failure_does_not_break_generation(self, monkeypatch):
        # `inject` 是**同步**函数：替身必须也同步。写成 async def 会让它返回协程，
        # 真正的报错变成「解包协程 TypeError」——测试蒙对，机制完全错（本轮自查抓到）。
        def boom(*a, **k):
            raise RuntimeError("卡包炸了")

        monkeypatch.setattr(skillwire, "inject", boom)
        ctx = _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch)
        assert "现在请写" in ctx, "任务段必须还在——正文照常生成"
        assert "【本章手艺参考" not in ctx, "装配失败不能留下半截手艺块"

    def test_db_read_failure_does_not_break_generation(self, monkeypatch):
        """t2b 给正文热路径**新增**了一次 DB 读（novel_texts），它抖动不能带崩整章。

        注意不能用 `_assemble()` 打这条补丁：那个 helper 内部会再把
        `novel_texts` 覆写成 `_corpus`，把这里的 `bad` 冲掉——
        第一版就是这么「通过」的，实际一条异常都没触发（自查抓到的假绿）。
        """
        async def bad(db, nid, limit=40):
            raise RuntimeError("DB 抖了一下")

        monkeypatch.setattr(skills_mod, "novel_texts", bad)
        p, novel, chapter, _job, _rows = _bundle(5)
        block, records = asyncio.run(
            factory_mod._chapter_skill_block(p, novel, chapter, _StubDb(), 5)
        )
        assert block == "", "DB 抖动时不该留下半截手艺块"
        assert records and any("装配异常" in n for n in records[0]["notes"])

    def test_records_still_come_back_on_failure(self, monkeypatch):
        """兜住异常不等于抹掉痕迹：record 要留下「为什么没注」。"""
        def boom(*a, **k):
            raise RuntimeError("卡包炸了")

        monkeypatch.setattr(skillwire, "inject", boom)
        p, novel, chapter, _job, _rows = _bundle(5)
        block, records = asyncio.run(factory_mod._chapter_skill_block(p, novel, chapter, None, 5))
        assert block == ""
        assert records and records[0]["node"] == "generate"
        assert any("装配异常" in n for n in records[0]["notes"]), records[0]["notes"]

    def test_wire_sink_collects_records(self, monkeypatch):
        """t3 落库接口：调用方传 list 就能接走正文节点的 record（评审 M-7）。"""
        sink: list = []
        _assemble(5, corpus=CORPUS, monkeypatch=monkeypatch, sink=sink)
        assert len(sink) == 2, f"方法论卡 + 文风实测各一条：{len(sink)}"
        assert all(r["node"] == "generate" for r in sink)

    def test_sink_defaults_to_none_and_keeps_positional_callers(self):
        """夜跑/批量按位置调用、不传 sink——新增参数必须可选（S1 零改动约束）。"""
        import inspect

        sig = inspect.signature(factory_mod._assemble_context)
        assert sig.parameters["wire_sink"].default is None


class TestStyleBudgetGuard:
    """`_STYLE_TOOL_BUDGET = 1500` 是任务书明写的数字，必须有守卫（评审 M-5）。

    变异「去掉 1500 上限」与「1500 → 6000」都跑出 0 failed——
    总量封顶仍成立，所以文风那一半被放大到多少都没人管。
    """

    @pytest.mark.parametrize("total,expect_style", [(6500, 1500), (4000, 1000), (2000, 500)])
    def test_split_matches_spec(self, monkeypatch, total, expect_style):
        seen: list = []
        orig_inject = skillwire.inject

        def spy(*a, **k):
            seen.append((k.get("mode"), k.get("max_total_chars")))
            return orig_inject(*a, **k)

        async def corpus(db, nid, limit=40):
            return CORPUS

        monkeypatch.setattr(skills_mod, "novel_texts", corpus)
        monkeypatch.setattr(skillwire, "inject", spy)
        monkeypatch.setitem(skillwire.NODE_BUDGET, "generate", total)

        p, novel, chapter, _job, _rows = _bundle(5)
        asyncio.run(factory_mod._chapter_skill_block(p, novel, chapter, None, 5))

        style = [v for mode, v in seen if mode == "tool_only"]
        cards = [v for mode, v in seen if mode != "tool_only"]
        assert style == [expect_style], f"总额 {total} 下文风份额应 {expect_style}，实得 {style}"
        assert cards == [total - expect_style], f"方法论卡应拿剩下的 {total - expect_style}，实得 {cards}"

    def test_two_shares_sum_to_node_budget(self, monkeypatch):
        """两半之和 == 节点总额，一分不多（这是「共享」而非「各吃满」的代数形式）。"""
        seen: list = []
        orig_inject = skillwire.inject

        def spy(*a, **k):
            seen.append(k.get("max_total_chars"))
            return orig_inject(*a, **k)

        async def corpus(db, nid, limit=40):
            return CORPUS

        monkeypatch.setattr(skills_mod, "novel_texts", corpus)
        monkeypatch.setattr(skillwire, "inject", spy)
        p, novel, chapter, _job, _rows = _bundle(5)
        asyncio.run(factory_mod._chapter_skill_block(p, novel, chapter, None, 5))
        assert sum(seen) == skillwire.NODE_BUDGET["generate"], seen


class TestAccountingFormulaHolds:
    """`chars = 手册 + 实测 + 参考` 在三种模式下都要成立（评审 M-1）。

    tool_only 曾把 `chars` 写死 0，按自家公式反推参考字数得 **-384**。
    """

    @pytest.mark.parametrize("mode", ["full", "ref_only", "tool_only"])
    @pytest.mark.parametrize("node", ["generate", "review", "deconstruct"])
    def test_reference_part_is_never_negative(self, node, mode):
        ch = [("第1章", "他握剑。雨落了一夜。" * 200)]
        _t, r = skillwire.inject(node, task="测试", mode=mode, chapters=ch, max_total_chars=3000)
        refs = r["chars"] - r["tool_chars"] - r["manual_chars"]
        assert refs >= 0, f"{node}/{mode}：反推参考字数 {refs} 为负，记账破了自家公式"
        assert r["chars"] <= 3000, f"{node}/{mode}：账面消耗超预算"


class TestHonestySurvivesAnyBudget:
    """诚实声明不得被截断牺牲（评审 M-2）。

    兜底分支原本裸切 `text[:budget]`，而 card_block 把【脚本说明】等声明全放在**尾部**，
    于是「该卡部分脚本由服务端真实执行」整句被削——deconstruct 预算被迫从 20000
    抬到 22000 才让那条断言过，属于用调参数盖症状。
    """

    @pytest.mark.parametrize("budget", [1500, 2500, 4000, 8000, 20000])
    def test_script_declaration_survives(self, budget):
        ch = [("第1章", "他握剑。雨落了一夜，灯一盏盏灭掉。" * 300)]
        text, _r = skillwire.inject("deconstruct", task="拆解这本参考书", chapters=ch,
                                    max_total_chars=budget)
        assert len(text) <= budget
        assert "该卡部分脚本由服务端真实执行" in text, f"预算 {budget} 下诚实声明被截没"

    def test_budget_is_no_longer_load_bearing_for_the_declaration(self, monkeypatch):
        """把 deconstruct 预算临时调回 20000，声明仍要在——证明抬预算不是唯一支撑。"""
        monkeypatch.setitem(skillwire.NODE_BUDGET, "deconstruct", 20000)
        text, _r = skillwire.inject("deconstruct", task="拆解这本参考书")
        assert "该卡部分脚本由服务端真实执行" in text
