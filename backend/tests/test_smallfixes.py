"""t5-smallfixes 三处小账的守卫（碧霄亲自实现，因本会话子代理派发连续失败）。

三处的**红绿性质不同**，如实标注，不假装都跑过红：

① `tests`/`ai_factory` 界面正文写作的 system **少了 ANTI_LLM_RULES**
  （nightly.py:151 与 batch.py:587 都有，唯独界面这条路径没有）
  → 本文件 `test_ui_chapter_system_carries_anti_llm_rules` **原先是红的**。
  判据用 AST：钉的是"那一段拼装里确实引用了这个常量"，不是全文出现次数——
  出现次数会因为注释、导入而虚高，那是**假绿**。

② 界面定稿路径调 `_update_state_files` **没传 include_particle=True**
  → `test_finalize_passes_include_particle_true` **原先是红的**。
  git 史实：nightly 那行注释写「与 finalize 一致，含资源账本」，
  作者**以为** finalize 本来就带账本 → UI 缺参是**疏漏不是有意关闭**，故应补齐。

③ `subplot_board` 恒空（**全仓零写入者**）
  → 两条测试**天生是绿的**（碧霄原任务书写错了：现状并非"无论有无都拼一行"，
  `ai_factory.py:1398` 本来就是 `if val:`，空时不拼）。它们的价值在变异上：
  若有人改成 `else "（未启用）"` 之类把空位喂给模型，立刻变红。
"""

import ast
import asyncio
import json
from pathlib import Path

import pytest

from app.routers import ai_factory as factory_mod
SOURCE = Path(factory_mod.__file__).resolve()
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))


def _chapter_system_assign():
    """找到"整章正文写作"那段 system 赋值节点（界面正文生成的 system 拼装处）。"""
    hits = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "system" for t in node.targets
        ):
            if "整章正文写作" in ast.dump(node.value) or "整章正文写作" in _literal_text(node.value):
                hits.append(node)
    assert len(hits) == 1, f"界面正文 system 拼装点应唯一，实得 {len(hits)} 处——判据锚点漂了"
    return hits[0]


def _literal_text(node) -> str:
    # 天演 MN-2：原写法内外两个 for 都叫 c，内层把外层变量遮蔽——功能对但极易误读。
    # 现内外异名：外层遍历 AST 节点取字符串常量，内层把常量拆成单字符。
    return "".join(
        ch
        for const in ast.walk(node)
        if isinstance(const, ast.Constant) and isinstance(const.value, str)
        for ch in const.value
    )


# ================================================================ ① ANTI_LLM_RULES


class TestUiAntiLlmParity:
    def test_ui_chapter_system_carries_anti_llm_rules(self):
        """界面正文生成的 system 必须带上 ANTI_LLM_RULES——与 nightly/batch 同一套反 AI 腔规则。

        这条**原先是红的**（system 只有 _SYSTEM + 一句"中文网文风格…"，没有规则常量）。
        """
        assign = _chapter_system_assign()
        names = {n.id for n in ast.walk(assign.value) if isinstance(n, ast.Name)}
        assert "ANTI_LLM_RULES" in names, (
            f"界面正文 system 拼装里没有 ANTI_LLM_RULES（实得引用：{sorted(names)}）——"
            "同一功能三处实现、行为不同的第 13 条老账"
        )

    def test_anti_llm_rules_module_imported_not_inlined(self):
        """必须**导入常量**而不是在界面侧重抄一份文案。

        重抄 = 两份规则各自漂移（这条判据与本项目"期望值不许从实现读"同源）。
        """
        imported = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
        assert "ANTI_LLM_RULES" in imported, "界面侧应 from .anti_llm import ANTI_LLM_RULES，不许复制粘贴规则文本"
        text = SOURCE.read_text(encoding="utf-8")
        from app.anti_llm import ANTI_LLM_RULES

        assert ANTI_LLM_RULES not in text, "规则正文不得被抄进 ai_factory.py（抄了就是一份会漂移的副本）"


# ================================================================ ② include_particle


class TestFinalizeParticleParity:
    def test_finalize_passes_include_particle_true(self, monkeypatch):
        """界面定稿必须带资源账本滚动（与夜跑/批量一致）。这条**原先是红的**。"""
        from test_fix7_draft import fake_user, install_finalize_guards, make_draft_bundle

        p, job, db = make_draft_bundle()
        job.status = "writing"
        install_finalize_guards(monkeypatch, p, db)

        seen: list[dict] = []

        # 评审修复轮（天演 MN-1 顺手项）：spy 同步收窄为显式新契约签名——
        # *a/**k 只记 kwargs 的写法对"调用面形状"零判据，显式签名少一个
        # 位置参数就 TypeError（形状护栏与 kb_sync 侧替身同口径）。
        async def spy(p, chapter, text, config, db, *, include_particle=False):
            seen.append({"include_particle": include_particle})
            return True

        monkeypatch.setattr(factory_mod, "_update_state_files", spy)
        data = factory_mod.FinalizeIn(content_text="这是一段足够长且合格的正文内容，已经超过了二十个字的最低门槛。")
        from fastapi import BackgroundTasks

        asyncio.run(factory_mod.finalize_chapter(1, 7, data, BackgroundTasks(), fake_user(), db))

        assert seen, "finalize 没调 _update_state_files，替身没接上——这条测试自身失效"
        assert seen[0].get("include_particle") is True, (
            f"界面定稿没传 include_particle=True（实得 {seen[0]}）——资源账本不会滚动"
        )

    def test_finalize_actually_writes_particle_ledger_back(self, monkeypatch):
        """天演 MJ-4：t5② 缺**行为级**测试——上面那条 spy 只看参数、不走路
        （把账本写回改成 if False，spy 照绿）。本条让**真实** _update_state_files
        跑完整段：LLM 替身返回含 particle_ledger 的状态 JSON，断言 p.particle_ledger
        真被写回。判据三重，互相印证测的是真实路径而非 spy：
        ① p.particle_ledger 等于替身给的新值；② p.global_summary 同步更新
        （spy 不会碰它）；③ 喂给 LLM 的 prompt 含【现有资源账本】段
        （证明 include_particle=True 流进了真实函数）。"""
        from test_fix7_draft import fake_user, install_finalize_guards, make_draft_bundle

        real_update_state_files = factory_mod._update_state_files  # guards 之前捕真身
        p, job, db = make_draft_bundle()
        job.status = "writing"
        # 真实函数要读写的状态字段（make_draft_bundle 的 SimpleNamespace 没带；
        # 缺了会被真实函数的 except 吞成 False，测试就测了个寂寞）
        p.global_summary = "旧摘要。"
        p.character_state = ""
        p.plot_arcs = ""
        p.particle_ledger = None

        new_ledger = "灵石 5200 两；储物袋 3 只；炼气三层（突破中）"
        new_summary = "第一章后：主角入局，宗门大比在即。"
        captured_prompts: list[str] = []

        async def state_json_chat(config, system, prompt, **kw):
            captured_prompts.append(prompt)
            return json.dumps({"global_summary": new_summary, "particle_ledger": new_ledger}, ensure_ascii=False)

        install_finalize_guards(monkeypatch, p, db)
        # guards 把 _update_state_files mock 成恒 True——本条测的恰恰是**真实写回
        # 路径**，这里覆盖回真身；_chat_text 换成吐状态 JSON 的替身（顺序必须在
        # guards 之后，否则被 guards 的版本盖掉）。
        monkeypatch.setattr(factory_mod, "_update_state_files", real_update_state_files)
        monkeypatch.setattr(factory_mod, "_chat_text", state_json_chat)

        data = factory_mod.FinalizeIn(content_text="这是一段足够长且合格的正文内容，已经超过了二十个字的最低门槛。")
        from fastapi import BackgroundTasks

        out = asyncio.run(factory_mod.finalize_chapter(1, 7, data, BackgroundTasks(), fake_user(), db))

        assert out["state_updated"] is True, "真实 _update_state_files 应返回 True（False 说明真实路径抛错被吞）"
        assert p.particle_ledger == new_ledger, (
            f"资源账本没有被真实写回（实得 {p.particle_ledger!r}）——include_particle 写回分支断了（天演 MJ-4）"
        )
        assert p.global_summary == new_summary, f"前情摘要也没更新（实得 {p.global_summary!r}）——真实路径根本没跑到"
        assert any("现有资源账本" in pr for pr in captured_prompts), (
            "prompt 里没有【现有资源账本】段——include_particle=True 没流进真实函数"
        )


# ================================================================ ③ subplot_board 空位


def _outline_continue_prompt(monkeypatch, subplot_board: str) -> str:
    """跑一遍大纲续写，抓它真正喂给模型的 prompt。

    替身**复用 test_t4_wiring 的现成工厂**（_full_project/_patch_llm/SeqDB/make_skeleton），
    不在这里重抄一份——两份替身各自漂移是本项目的老病。
    """
    from app.models import AiProject
    from test_fix8_outline_continue import make_skeleton
    from test_t4_wiring import SeqDB, _full_project, _patch_llm, _run, fake_user

    captured: list[str] = []
    reply = json.dumps(
        {"volumes": [{"title": "第四卷", "chapters": [{"title": "风波", "outline": "事件"}]}]},
        ensure_ascii=False,
    )

    async def spy_chat(config, system, prompt, **kw):
        captured.append(prompt)
        return reply

    p = _full_project(status="writing")
    p.subplot_board = subplot_board
    _patch_llm(monkeypatch, reply)                       # 先装基础替身（_pick_config 等）
    monkeypatch.setattr(factory_mod, "_chat_text", spy_chat)  # 再用 spy 接管，顺序不能反
    vols, chaps, jobs = make_skeleton(30, 10)
    db = SeqDB(rows={AiProject: p}, results=[jobs, chaps, vols])
    _run(factory_mod.outline_continue(1, fake_user(), db))
    assert captured, "没抓到 prompt，替身失效"
    return captured[-1]


class TestSubplotBoardHonestSlot:
    """`subplot_board` 全仓**零写入者**（models.py:395 定义、db.py:34 建列、
    ai_factory.py:709 吐给前端、:1398 读进 prompt——没有任何一处赋值）。
    本次只钉"空数据不许占位喂模型"，**不实现支线板功能**。
    这两条**天生是绿的**（现状 `if val:` 已满足），守的是未来别改坏。"""

    def test_empty_subplot_board_does_not_occupy_a_slot(self, monkeypatch):
        prompt = _outline_continue_prompt(monkeypatch, "")
        assert "支线板" not in prompt, f"空支线板仍被拼成一行喂给模型（模型会以为该有支线信息）：{prompt[:300]}"

    def test_nonempty_subplot_board_is_shown(self, monkeypatch):
        prompt = _outline_continue_prompt(monkeypatch, "A线：主角已入宗门；B线：家族暗线未启")
        assert "支线板" in prompt and "A线" in prompt, f"有内容时应当显示，实得：{prompt[:300]}"


# ================================================================ ④ 天演 MJ-1：字数契约不得进 ANTI_LLM_RULES

# 期望串硬编码在这里，不从实现读。覆盖：显式字数口径（硬下限/不少于/字数）、
# 弹性口径（约/±）、配置引用口径（target_words）。
WORDCOUNT_PHRASES_BANNED_IN_ANTI_LLM_RULES = ("硬下限", "不少于", "字数", "约", "±", "target_words")


def _anti_llm_rules_constant_text() -> str:
    """从 app/anti_llm.py 的 AST 取 ANTI_LLM_RULES 赋值的**字面值**。

    判据对象是常量本身、不是全文件文本——`rewrite_prompt` 等别处的字数措辞
    （"±15%"）不受本判据管辖，只挡"把字数契约塞进 system 侧文风铁律"。
    """
    import app.anti_llm as anti_mod

    tree = ast.parse(Path(anti_mod.__file__).resolve().read_text(encoding="utf-8"))
    hits = [
        node
        for node in tree.body  # 只看模块级：函数体内的局部遮蔽由另一条判据管
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "ANTI_LLM_RULES" for t in node.targets)
    ]
    assert len(hits) == 1, f"ANTI_LLM_RULES 模块级赋值应唯一，实得 {len(hits)} 处——判据锚点漂了"
    value = hits[0].value
    assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
        "ANTI_LLM_RULES 应是模块级纯字符串字面量——改成 f-string/拼接会让本判据失效，属于规避审查"
    )
    return value.value


class TestAntiLlmRulesNoWordcountContract:
    """天演 MJ-1：字数契约护栏盯不到 system 侧。

    根因：三条字数不变量断言的都是 `_assemble_context` 产出的 **user 侧**，
    而 ANTI_LLM_RULES 是**system 侧**的常量——往常量里塞「不少于 3000 字」，
    既有测试全绿，性质成立只靠人工复核。本条把判据钉到常量本身：
    这些字数口径字样一旦进常量，立刻红。**天生是绿的**（现状常量干净），
    价值在变异上。
    """

    @pytest.mark.parametrize("banned", WORDCOUNT_PHRASES_BANNED_IN_ANTI_LLM_RULES)
    def test_anti_llm_rules_constant_carries_no_wordcount_phrases(self, banned):
        rules = _anti_llm_rules_constant_text()
        assert banned not in rules, (
            f"ANTI_LLM_RULES 常量出现了字数口径字样「{banned}」——字数契约只能走"
            f"user 侧上下文与配置口径，不许写死进 system 侧文风铁律"
            f"（三条既有字数不变量只盯 user 侧，盯不到这里）；常量头 80 字：{rules[:80]!r}"
        )


# ================================================================ ⑤ 天演 MJ-2：防同名局部遮蔽


def _assign_target_names(node) -> list:
    """赋值节点的目标名字（Assign 多目标 / AnnAssign·AugAssign 单目标）。"""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


class TestAntiLlmRulesNotShadowed:
    """天演 MJ-2：AST 引用判据可被**同名局部赋值**绕过——在 generate_chapter
    函数体内写一行 `ANTI_LLM_RULES = "50字劣化副本"`，既有判据看到的是被遮蔽的
    名字本身，照绿。补一道禁令：ai_factory.py 全文件任何位置**不许出现**对
    ANTI_LLM_RULES 的 Assign/AnnAssign/AugAssign，该名字只许来自模块级 import。
    **天生是绿的**（现状只有 import），价值在变异上。"""

    def test_no_assignment_shadows_anti_llm_rules_anywhere(self):
        shadowed = [
            (node.lineno, names)
            for node in ast.walk(TREE)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
            for names in [_assign_target_names(node)]
            if "ANTI_LLM_RULES" in names
        ]
        assert not shadowed, (
            f"ai_factory.py 出现了对 ANTI_LLM_RULES 的赋值（行号与目标：{shadowed}）——"
            "该名字只许 from .anti_llm import 进来，任何函数作用域内的赋值都是"
            "劣化副本遮蔽真规则（天演 MJ-2）"
        )


# ================================================================ ⑥ 天演 MJ-3：三路径齐备


def _assert_assembly_references_anti_llm_rules(mod, target_name: str, anchor: str):
    """与界面同款的 AST 引用断言：在模块源码里找「target_name = …含 anchor…」的
    赋值节点（应唯一），断言它的值里引用了 ANTI_LLM_RULES 名字。

    钉的是"那一段拼装确实引用了这个常量"，不是全文出现次数——全文出现
    会因注释/导入虚高，摘掉一处拼装后照样绿，那是**假绿**（天演 MJ-3）。
    """
    tree = ast.parse(Path(mod.__file__).resolve().read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == target_name for t in node.targets
        ):
            if anchor in _literal_text(node.value) or anchor in ast.dump(node.value):
                hits.append(node)
    assert len(hits) == 1, (
        f"{mod.__name__} 里 {target_name} 赋值含「{anchor}」应唯一，实得 {len(hits)} 处——判据锚点漂了"
    )
    names = {n.id for n in ast.walk(hits[0].value) if isinstance(n, ast.Name)}
    assert "ANTI_LLM_RULES" in names, (
        f"{mod.__name__} 的 {target_name} 拼装（「{anchor}」处）没有引用 ANTI_LLM_RULES"
        f"（实得引用：{sorted(names)}）——任务书 §3① 要求三条路径齐备，缺一即假"
    )


class TestNightlyBatchAntiLlmParity:
    """天演 MJ-3：「三路径齐备」此前实际只守了 1/3——摘掉 nightly.py:151 的
    `+ ANTI_LLM_RULES`，t5 全部测试照绿。任务书 §3① 字面要求三条路径各构造一次：
    界面（① 已守）/ 夜跑 / 批量。夜跑与批量的拼装点各钉一条（batch 的两处
    按真实情况分别钉 system 与修订 prompt，不做恒真检查）。**天生是绿的**，
    价值在变异上。"""

    def test_nightly_chapter_system_carries_anti_llm_rules(self):
        from app import nightly as nightly_mod

        _assert_assembly_references_anti_llm_rules(nightly_mod, "system", "整章正文写作")

    def test_batch_stream_chapter_system_carries_anti_llm_rules(self):
        from app.routers import batch as batch_mod

        _assert_assembly_references_anti_llm_rules(batch_mod, "system", "整章正文写作")

    def test_batch_revise_prompt_carries_anti_llm_rules(self):
        from app.routers import batch as batch_mod

        _assert_assembly_references_anti_llm_rules(batch_mod, "prompt", "遵守以下反 AI 味写作规则")
