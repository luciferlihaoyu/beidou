# -*- coding: utf-8 -*-
"""t6-chapter-stages：章节内角色接力 + 断点续跑。单文件、零真实网络、零真实模型、<30 秒。

被钉死的行为契约（docs/批次二-章节分段与三路去AI味-计划.md §3）：
- 三档：off（默认，与改前逐字节一致）/ standard（写作→审校→修订）/ deep（六段全跑）。
- 每段产出**立刻**落盘 job.stage_json；中断重入跳过已完成段（断点续跑）。
- 段间只传上一段产出，不重传全量 context（open-skoob 的成本缺陷不学）。
- 结算段不调模型：产出喂给现有 _update_state_files。
- 迁移走 app/db.py 的 _ensure_column，幂等。

诚实口径：
- EXPECT_* 常量是**改动 nightly.py 之前**用探针抓的实拍（_off_expect_generated.py，
  AUTOMATIC PROBE OUTPUT），不是从实现现读——off 一致性天生绿，靠变异自证咬合。
- 断点续跑/段间传递判据：断言具体调用序列（fake 门解析「【段：X】」前缀），
  不只数调用次数。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python -m pytest tests/test_chapter_stages.py -q
"""

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import _off_expect_generated as EXPECT  # 同目录探针实拍常量（pytest 自动加 sys.path）
from app import nightly
from app.db import SessionLocal, init_db
from app.models import AiChapterJob, AiProject, User


# ---------------------------------------------------------------- 替身世界

def make_world(pipeline="off"):
    """与探针同形状的最小世界：p/novel/chapter/job/db 全是替身，零真库。"""
    p = SimpleNamespace(
        id=1,
        user_id=1,
        chapter_pipeline=pipeline,
        author_intent=EXPECT.FAKE_AUTHOR_INTENT,
        current_focus=EXPECT.FAKE_CURRENT_FOCUS,
        target_chapter_words=None,
        book_spec_json='{"genre": "仙侠"}',
        style_notes="",
        global_summary="",
        character_state="",
        plot_arcs="",
        tokens_prompt=0,
        tokens_completion=0,
        chapter_llm=None,
        summary_llm=None,
        context_recent_chapters=2,
        context_extra_chapters="[]",
        kb_query="",
    )
    novel = SimpleNamespace(id=5, title="断玉记")
    chapter = SimpleNamespace(id=77, title=EXPECT.FAKE_CHAPTER_TITLE, content="", word_count=0)
    job = SimpleNamespace(
        id=900,
        project_id=1,
        chapter_id=77,
        status="pending",
        outline=EXPECT.FAKE_OUTLINE,
        summary="",
        actual_words=0,
        attempt=0,
        started_at=None,
        last_error="",
        last_error_code="",
        stage_json="",
    )

    class StubDB:
        def __init__(self):
            self.commits = 0

        async def get(self, model, pk):
            return SimpleNamespace(id=1, role="user")

        async def commit(self):
            self.commits += 1

        async def execute(self, stmt):
            raise AssertionError("分段路径不应查库（_assemble_context 等已替）")

    return p, novel, chapter, job, StubDB()


class FakeGate:
    """假模型门：按脚本吐产出、可注入异常、记录每次收到的 prompt 与解析出的段名。

    段名识别靠段指令前缀「【段：X】」——这是对实现的硬约束：调用序列断言的具体性
    全靠它，不许退化成"数次数"。
    attempted = 发出过的全部调用（含抛异常的）；completed = 成功返回的调用。
    """

    def __init__(self, script):
        self.script = list(script)
        self.attempted: list[str] = []
        self.completed: list[str] = []
        self.prompts: list[str] = []
        self.systems: list[str] = []

    async def __call__(self, llm, system, prompt, *, max_tokens=4000, usage_sink=None):
        self.systems.append(system)
        self.prompts.append(prompt)
        m = re.match(r"【段：(.+?)】", prompt)
        assert m is not None, f"分段 prompt 必须以【段：X】开头，实得前 30 字：{prompt[:30]!r}"
        self.attempted.append(m.group(1))
        action = self.script[len(self.attempted) - 1]
        if isinstance(action, Exception):
            raise action
        self.completed.append(m.group(1))
        return action


# 六段产出：每段带唯一标记串，供段间传递断言（第 4 段 prompt 只见第 3 段产出）
S_PLAN = "【规划产出标记·PLAN-MARK】本章目标：揭示断玉第二道刻痕的秘密。核心冲突：林昭逼问沈青梧。开场：石桌对峙。章末钩子：先看刻痕再开盒。"
S_OUTLINE = "【编排产出标记·OUTLINE-MARK】场景一：石桌前按玉对峙。场景二：沈青梧道出旧事。场景三：林昭转身下山，留一句叮嘱。"
S_WRITE = (
    "【写作产出标记·WRITE-MARK】林昭把断玉按在石桌上，指节泛白。"
    "「第二道刻痕，你早就知道。」沈青梧盯着雾气，半晌才开口：你爹当年也是这么问的。"
    "风从崖底卷上来，他忽然明白自己问了三年的问题，答案一直写在这张脸上。"
    "他转身下山，身后追来一句：玉合上之前，先看刻痕，再开盒。山道盘进林子，两个影子各自散了。"
)
S_REVIEW = "【审校产出标记·REVIEW-MARK】审校后全文：林昭把断玉按在石桌上。刻痕对月。「第二道刻痕，你早就知道。」沈青梧望着崖底：你爹当年也这么问。风卷上来，他不再问第三年。转身时身后追来一句——先看刻痕，再开盒。"
S_REVISE = "【修订产出标记·REVISE-MARK】终稿：断玉压着石桌，新刻的痕对着月光。林昭开口，声音压得极低：第二道刻痕，你早知道。沈青梧看向崖底，把三年前的旧事还给他。风停在他转身的那一刻，身后只剩一句叮嘱——先看刻痕，再开盒。"

DEEP_SCRIPT = [S_PLAN, S_OUTLINE, S_WRITE, S_REVIEW, S_REVISE]
DEEP_STAGE_NAMES = ["规划", "编排", "写作", "审校", "修订"]
STANDARD_SCRIPT = [S_WRITE, S_REVIEW, S_REVISE]
STANDARD_STAGE_NAMES = ["写作", "审校", "修订"]


class SettlementSpies:
    """结算替身：记录 _update_state_files 收到的正文、split/关系同步是否被触达。"""

    def __init__(self):
        self.state_files_texts: list[str] = []
        self.sync_texts: list[str] = []
        self.split_texts: list[str] = []
        self.summary_prompts: list[str] = []

    def install(self, monkeypatch):
        async def fake_state_files(p, chapter, t, llm, db, include_particle=True):
            self.state_files_texts.append(t)
            return True

        async def fake_sync(p, novel, t, llm, db):
            self.sync_texts.append(t)

        async def fake_split(p, novel, job, chapter, t, db):
            self.split_texts.append(t)
            return 1

        async def fake_chat_text(llm, system, prompt, *, max_tokens=4000, usage_sink=None):
            self.summary_prompts.append(prompt)
            return "摘要桩：林昭与沈青梧决裂。"

        async def fake_sync_chapter(db, chapter_id):
            return None

        import app.chapter_stages as cs
        import app.search_fts as search_fts

        monkeypatch.setattr(cs, "_update_state_files", fake_state_files)
        monkeypatch.setattr(cs, "_sync_relations_from_chapter", fake_sync)
        monkeypatch.setattr(cs, "_maybe_split_chapter", fake_split)
        monkeypatch.setattr(cs, "_chat_text", fake_chat_text)
        monkeypatch.setattr(cs, "_ensure_min_words", fake_chat_text)  # target=None 不会触发
        monkeypatch.setattr(search_fts, "sync_chapter", fake_sync_chapter)


def install_stage_context(monkeypatch, gate):
    """给 chapter_stages 装上其余替身：context / 配置 / detect。"""
    import app.chapter_stages as cs

    async def fake_assemble_context(p, novel, chapter, job, db, *a, **kw):
        return EXPECT.FAKE_CONTEXT

    async def fake_pick_config(user, db, route_field):
        return SimpleNamespace(id=9, name="fake", base_url="https://upstream.invalid", api_key="k", model="m")

    def fake_detect(t):
        return {"score": 85, "issues": []}

    monkeypatch.setattr(cs, "_assemble_context", fake_assemble_context)
    monkeypatch.setattr(cs, "_pick_config", fake_pick_config)
    monkeypatch.setattr(cs, "detect", fake_detect)
    monkeypatch.setattr(cs, "_stage_chat", gate)


def run_staged(world, gate=None):
    """跑一次分段入口并返回 (result, gate)。"""
    import app.chapter_stages as chapter_stages

    p, novel, chapter, job, db = world
    gate = gate or FakeGate(DEEP_SCRIPT)
    result = asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))
    return result, gate


# ---------------------------------------------------------------- 1. 迁移（判据红）

def test_migration_columns_present_and_defaults():
    """init_db 后两表有新列，ORM 新行读回默认值 off / {}。"""

    async def main():
        await init_db()

    asyncio.run(main())

    async def cols(table):
        from app.db import engine

        async with engine.connect() as conn:
            rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        return [r[1] for r in rows]

    projects_cols = asyncio.run(cols("ai_projects"))
    jobs_cols = asyncio.run(cols("ai_chapter_jobs"))
    assert projects_cols.count("chapter_pipeline") == 1, f"ai_projects 缺 chapter_pipeline 列：{projects_cols}"
    assert jobs_cols.count("stage_json") == 1, f"ai_chapter_jobs 缺 stage_json 列：{jobs_cols}"

    async def insert_and_read():
        async with SessionLocal() as s:
            u = User(username="t6_mig_user", password_hash="x", role="admin")
            s.add(u)
            await s.flush()
            proj = AiProject(user_id=u.id, seed_prompt="迁移默认值验证")
            s.add(proj)
            await s.flush()
            job = AiChapterJob(project_id=proj.id)
            s.add(job)
            await s.flush()
            pid, jid = proj.id, job.id
            await s.commit()
        async with SessionLocal() as s:
            proj = await s.get(AiProject, pid)
            job = await s.get(AiChapterJob, jid)
            return proj.chapter_pipeline, job.stage_json

    pipeline, stage_json = asyncio.run(insert_and_read())
    assert pipeline == "off", f"chapter_pipeline 默认值应为 off，实得 {pipeline!r}"
    assert stage_json == "{}", f"stage_json 默认值应为 '{{}}'，实得 {stage_json!r}"


def test_migration_idempotent_run_twice():
    """连跑两次 init_db：不报错、列不重复加。"""

    async def main():
        await init_db()
        await init_db()

    asyncio.run(main())

    async def count_cols(table, column):
        from app.db import engine

        async with engine.connect() as conn:
            rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
        return sum(1 for r in rows if r[1] == column)

    assert asyncio.run(count_cols("ai_projects", "chapter_pipeline")) == 1
    assert asyncio.run(count_cols("ai_chapter_jobs", "stage_json")) == 1


# ---------------------------------------------------------------- 2. off 逐字节一致（天生绿锚）

def _install_off_stubs(monkeypatch):
    """与探针完全同面的替身：context/配置/检测/拆分/状态文件/关系同步/FTS。"""
    captured = []

    async def fake_chat_text(config, system, prompt, *, max_tokens=4000, usage_sink=None, **kw):
        captured.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
        return EXPECT.FAKE_BODY

    async def fake_assemble_context(p, novel, chapter, job, db, *a, **kw):
        return EXPECT.FAKE_CONTEXT

    async def fake_pick_config(user, db, route_field):
        return SimpleNamespace(id=9, name="fake", base_url="https://upstream.invalid", api_key="k", model="m")

    async def fake_split(p, novel, job, chapter, t, db):
        return 1

    async def fake_state_files(p, chapter, t, llm, db, include_particle=True):
        return True

    async def fake_sync(p, novel, t, llm, db):
        return 0

    def fake_detect(t):
        return {"score": 85, "issues": []}

    async def fake_sync_chapter(db, chapter_id):
        return None

    monkeypatch.setattr(nightly, "_chat_text", fake_chat_text)
    monkeypatch.setattr(nightly, "_assemble_context", fake_assemble_context)
    monkeypatch.setattr(nightly, "_pick_config", fake_pick_config)
    monkeypatch.setattr(nightly, "_maybe_split_chapter", fake_split)
    monkeypatch.setattr(nightly, "_update_state_files", fake_state_files)
    monkeypatch.setattr(nightly, "_sync_relations_from_chapter", fake_sync)
    monkeypatch.setattr(nightly, "detect", fake_detect)
    import app.search_fts as search_fts

    monkeypatch.setattr(search_fts, "sync_chapter", fake_sync_chapter)
    return captured


def test_off_prompt_byte_identical(monkeypatch):
    """off 时喂给模型的 system/prompt/预算与改动前探针实拍**逐字节**一致。

    判据来源：tests/_off_expect_generated.py = 改动 nightly.py 之前抓的实拍。
    本测试天生绿（它钉的是"现状不变"），咬合力靠变异靶③（off 分支污染）自证。
    """
    captured = _install_off_stubs(monkeypatch)
    p, novel, chapter, job, db = make_world(pipeline="off")
    result = asyncio.run(nightly._generate_chapter(p, novel, job, chapter, 7, db))

    assert len(captured) == 2, f"off 应恰好两笔模型调用（主生成+定稿摘要），实得 {len(captured)}"
    assert captured[0]["system"] == EXPECT.EXPECT_SYSTEM_MAIN, "off 主生成 system 与改前实拍不一致"
    assert captured[0]["prompt"] == EXPECT.EXPECT_PROMPT_MAIN, "off 主生成 prompt 与改前实拍不一致"
    assert captured[0]["max_tokens"] == EXPECT.EXPECT_MAIN_MAX_TOKENS
    assert captured[1]["system"] == EXPECT.EXPECT_SYSTEM_SUMMARY
    assert captured[1]["prompt"] == EXPECT.EXPECT_PROMPT_SUMMARY, "off 摘要 prompt 与改前实拍不一致"
    assert captured[1]["max_tokens"] == EXPECT.EXPECT_SUMMARY_MAX_TOKENS
    # 无分段标记：off prompt 里不得出现分段指令形状
    for c in captured:
        assert "【段：" not in c["prompt"] and "【段：" not in c["system"]
    # 战报同样逐字段钉死（探针实拍）
    assert result == EXPECT.EXPECT_RESULT_OFF, f"off 战报与改前实拍不一致：{result} vs {EXPECT.EXPECT_RESULT_OFF}"


def test_off_missing_attr_defaults_off(monkeypatch):
    """旧对象没有 chapter_pipeline 属性（getattr 兜底）也必须走原路。"""
    captured = _install_off_stubs(monkeypatch)
    p, novel, chapter, job, db = make_world(pipeline="off")
    del p.__dict__["chapter_pipeline"]  # 制造"旧形状"对象
    result = asyncio.run(nightly._generate_chapter(p, novel, job, chapter, 7, db))
    assert len(captured) == 2
    assert captured[0]["prompt"] == EXPECT.EXPECT_PROMPT_MAIN
    assert result == EXPECT.EXPECT_RESULT_OFF


def test_off_unknown_mode_walks_legacy_path(monkeypatch):
    """未知档位一律按 off 处理（保守降级），不得误入分段路径。"""
    captured = _install_off_stubs(monkeypatch)
    p, novel, chapter, job, db = make_world(pipeline="bogus")
    result = asyncio.run(nightly._generate_chapter(p, novel, job, chapter, 7, db))
    assert len(captured) == 2
    assert captured[0]["prompt"] == EXPECT.EXPECT_PROMPT_MAIN
    assert result == EXPECT.EXPECT_RESULT_OFF


# ---------------------------------------------------------------- 3. 断点续跑（核心证据）

def test_deep_full_run_calls_five_stages_and_settles(monkeypatch):
    """deep 完整跑：门调用序列恰为五段（具体序列断言），结算零模型调用，六键落盘。"""
    spies = SettlementSpies()
    spies.install(monkeypatch)
    gate = FakeGate(DEEP_SCRIPT)
    import app.chapter_stages as chapter_stages

    install_stage_context(monkeypatch, gate)
    p, novel, chapter, job, db = make_world(pipeline="deep")
    result = asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))

    assert gate.completed == DEEP_STAGE_NAMES, f"deep 完成段序列应为 {DEEP_STAGE_NAMES}，实得 {gate.completed}"
    assert gate.attempted == DEEP_STAGE_NAMES
    assert result["ok"] is True
    state = json.loads(job.stage_json)
    assert set(state.keys()) == {"规划", "编排", "写作", "审校", "修订", "结算"}, f"stage_json 六键：{set(state.keys())}"
    assert state["规划"]["text"] == S_PLAN and state["修订"]["text"] == S_REVISE
    assert state["规划"]["at"] and state["结算"]["at"], "每段产出须带时间戳"
    # 结算不调模型：门恰 5 笔；摘要（现有收尾）恰 1 笔
    assert len(gate.completed) == 5
    assert len(spies.summary_prompts) == 1
    # 结算产出喂给现有 _update_state_files 的正是最后一个 LLM 段（修订）产出
    assert spies.state_files_texts == [S_REVISE], "结算必须把最后一段产出喂给 _update_state_files"
    assert spies.sync_texts == [S_REVISE]
    assert job.status == "done" and result["state_updated"] is True


def test_resume_reentry_zero_calls_identical_result(monkeypatch):
    """跑完后的 job 重入：门调用 0 次，战报与第一次完全相同。"""
    spies = SettlementSpies()
    spies.install(monkeypatch)
    import app.chapter_stages as chapter_stages

    gate = FakeGate(DEEP_SCRIPT)
    install_stage_context(monkeypatch, gate)
    p, novel, chapter, job, db = make_world(pipeline="deep")
    r1 = asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))
    n_calls = len(gate.completed)

    r2 = asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))

    assert len(gate.completed) == n_calls, f"重入不得再调模型：第一次 {n_calls} 次，重入后 {len(gate.completed)} 次"
    assert r2 == r1, "重入战报必须与第一次完全一致"
    state = json.loads(job.stage_json)
    assert state["结算"]["text"] == json.dumps(r1, ensure_ascii=False)


def test_interrupt_at_stage3_resumes_from_exact_sequence(monkeypatch):
    """中断模拟：第 3 段抛异常 → 前 2 段已落盘；重入调用序列恰为第 3~5 段。"""
    spies = SettlementSpies()
    spies.install(monkeypatch)
    import app.chapter_stages as chapter_stages

    script = [S_PLAN, S_OUTLINE, RuntimeError("模拟第 3 段中断"), S_WRITE, S_REVIEW, S_REVISE]
    gate = FakeGate(script)
    install_stage_context(monkeypatch, gate)
    p, novel, chapter, job, db = make_world(pipeline="deep")

    # 第一次：第 3 段（写作）抛异常，异常向上冒
    with pytest.raises(RuntimeError, match="模拟第 3 段中断"):
        asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))

    assert gate.completed == ["规划", "编排"], f"中断前应恰跑完前 2 段，实得 {gate.completed}"
    assert gate.attempted == ["规划", "编排", "写作"], "第 3 段（写作）应尝试过并抛异常"
    state = json.loads(job.stage_json)
    assert list(state.keys()) == ["规划", "编排"], f"中断时已落盘的段：{list(state.keys())}"
    assert state["编排"]["text"] == S_OUTLINE
    assert db.commits >= 2, f"前 2 段产出必须各自 commit 落盘，实得 commits={db.commits}"

    # 重入：同一 gate（脚本按全局序号继续）+ 同一 job
    r2 = asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))
    # 断言具体续跑序列（不是"次数对"）：第 3 段尝试两次（第一次抛异常、重入成功），
    # completed 恰为五段全序列
    assert gate.attempted == ["规划", "编排", "写作", "写作", "审校", "修订"], (
        f"重入应恰从第 3 段续跑（写作尝试两次），实得 {gate.attempted}"
    )
    assert gate.completed == DEEP_STAGE_NAMES, f"重入后完成段序列应为 {DEEP_STAGE_NAMES}，实得 {gate.completed}"
    assert gate.completed.index("写作") == 2, "第 3 段（写作）必须从中断点续跑"
    assert r2["ok"] is True

    # 对照组：干净世界一次跑完 → 战报与五段产出逐字节一致
    gate_full = FakeGate(DEEP_SCRIPT)
    install_stage_context(monkeypatch, gate_full)
    p2, novel2, chapter2, job2, db2 = make_world(pipeline="deep")
    r_full = asyncio.run(chapter_stages.run_staged_chapter(p2, novel2, job2, chapter2, 7, db2))
    assert r2 == r_full, "中断续跑的战报必须与一次性完整跑完全一致"
    s_resume = json.loads(job.stage_json)
    s_full = json.loads(job2.stage_json)
    for key in DEEP_STAGE_NAMES + ["结算"]:
        assert s_resume[key]["text"] == s_full[key]["text"], f"段 {key} 产出与完整跑不一致"


def test_resume_reads_prev_output_from_disk_not_model(monkeypatch):
    """重入时第 3 段的输入必须取自落盘的上一段产出（OUTLINE-MARK），而非重新调模型。"""
    spies = SettlementSpies()
    spies.install(monkeypatch)
    import app.chapter_stages as chapter_stages

    script = [S_PLAN, S_OUTLINE, RuntimeError("boom"), S_WRITE, S_REVIEW, S_REVISE]
    gate = FakeGate(script)
    install_stage_context(monkeypatch, gate)
    p, novel, chapter, job, db = make_world(pipeline="deep")
    with pytest.raises(RuntimeError):
        asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))
    gate.prompts.clear()
    asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))
    resume_write_prompt = gate.prompts[0]
    assert "【段：写作】" in resume_write_prompt
    assert "OUTLINE-MARK" in resume_write_prompt, "续跑第 3 段必须以落盘的编排产出为输入"
    assert "PLAN-MARK" not in resume_write_prompt, "不得重传更早历史"


# ---------------------------------------------------------------- 4. 段间只传上一段产出

def test_stage4_prompt_carries_only_previous_stage(monkeypatch):
    """deep 第 4 段（审校）prompt：只含第 3 段产出，不含第 1/2 段产出、不含全量 context。"""
    spies = SettlementSpies()
    spies.install(monkeypatch)
    gate = FakeGate(DEEP_SCRIPT)
    import app.chapter_stages as chapter_stages

    install_stage_context(monkeypatch, gate)
    p, novel, chapter, job, db = make_world(pipeline="deep")
    asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))

    p4 = gate.prompts[3]
    assert "【段：审校】" in p4
    assert "WRITE-MARK" in p4, "第 4 段应以第 3 段（写作）产出为输入"
    assert "PLAN-MARK" not in p4, "第 4 段不得携带第 1 段产出（全量历史重传缺陷）"
    assert "OUTLINE-MARK" not in p4, "第 4 段不得携带第 2 段产出"
    assert "【测试上下文·固定桩】" not in p4, "第 4 段不得重传全量 context"
    assert "【上一环节产出】" in p4
    # 同理抽查第 5 段：只见第 4 段
    p5 = gate.prompts[4]
    assert "REVIEW-MARK" in p5 and "WRITE-MARK" not in p5 and "OUTLINE-MARK" not in p5


def test_standard_mode_three_stages_first_gets_full_context(monkeypatch):
    """standard：恰三段（写作→审校→修订）；写作是第 1 段，拿全量 context 原样。"""
    spies = SettlementSpies()
    spies.install(monkeypatch)
    gate = FakeGate(STANDARD_SCRIPT)
    import app.chapter_stages as chapter_stages

    install_stage_context(monkeypatch, gate)
    p, novel, chapter, job, db = make_world(pipeline="standard")
    result = asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))

    assert gate.completed == STANDARD_STAGE_NAMES, f"standard 完成段序列应为 {STANDARD_STAGE_NAMES}，实得 {gate.completed}"
    assert gate.prompts[0].endswith(EXPECT.FAKE_CONTEXT), "standard 第 1 段（写作）必须拿到全量 context 原样"
    assert result["ok"] is True
    state = json.loads(job.stage_json)
    assert set(state.keys()) == {"写作", "审校", "修订", "结算"}
    assert spies.state_files_texts == [S_REVISE]


def test_unknown_pipeline_value_is_rejected_by_executor(monkeypatch):
    """执行器对未知档位拒绝执行（分流层已兜底，这里钉第二道防线）。"""
    import app.chapter_stages as chapter_stages

    p, novel, chapter, job, db = make_world(pipeline="bogus")
    with pytest.raises(ValueError):
        asyncio.run(chapter_stages.run_staged_chapter(p, novel, job, chapter, 7, db))


# ---------------------------------------------------------------- 8. 中断的成本账（评审 A-1 major）


class UsageGate(FakeGate):
    """假模型门 + 真往 usage_sink 里填数——因为 usage_sink 是**实现与本模块的唯一记账通道**，
    不填它就没有成本证据（评审书 D1：期望值必须硬编码在测试侧）。"""

    def __init__(self, plan):
        super().__init__([item[0] if isinstance(item, tuple) else item for item in plan])
        self.plan = list(plan)
        self.spent = []

    async def __call__(self, llm, system, prompt, *, max_tokens=4000, usage_sink=None):
        item = self.plan[len(self.attempted)]  # 本次将执行的那一项
        stage = re.match(r"【段：(.+?)】", prompt).group(1)
        if isinstance(item, tuple):
            text, pr, co = item
            if usage_sink is not None:
                usage_sink["prompt"] = pr
                usage_sink["completion"] = co
            self.spent.append((stage, pr, co))
            self.attempted.append(stage)
            self.completed.append(stage)
            self.prompts.append(prompt)
            return text
        self.attempted.append(stage)
        self.prompts.append(prompt)
        raise item  # 炸在这一段


def test_interrupted_run_still_books_the_tokens_already_spent(monkeypatch):
    """🔴 评审 A-1 major：段产出落盘了，**同一段的成本账却随栈销毁**。

    机理：`sinks` 是 run_staged_chapter 的局部 list，异常一抛就没了，而记账只在
    结算区做——于是"中断后重入"的章，中断前那几段真花掉的钱**永久少记**
    （天演实测 1500 只记 1200，漏 300）。
    这条判据钉的是：**已花的钱必须先入账，再往上抛**。
    """
    import app.chapter_stages as cs

    world = make_world("deep")
    gate = UsageGate(
        [
            (S_PLAN, 100, 10),
            (S_OUTLINE, 200, 20),
            RuntimeError("第 3 段上游炸了"),
        ]
    )
    recorded: list[tuple[int, int]] = []
    monkeypatch.setattr(cs, "_record_usage", lambda p, pr, co: recorded.append((pr, co)))
    install_stage_context(monkeypatch, gate)

    with pytest.raises(RuntimeError, match="第 3 段上游炸了"):
        run_staged(world, gate=gate)

    assert recorded == [(300, 30)], (
        f"中断前两段的成本必须入账（期望 300/30），实得 {recorded}"
        "——漏记即『章失败了但钱追不到』"
    )


def test_resume_after_interrupt_does_not_double_book(monkeypatch):
    """重入时已落盘的段跳过（不发调用、不记账），只记续跑的那几段——
    两次相加正好等于全程成本，**不许重复记账**。"""
    import app.chapter_stages as cs

    world = make_world("deep")
    spied = SettlementSpies()
    spied.install(monkeypatch)
    recorded: list[tuple[int, int]] = []
    monkeypatch.setattr(cs, "_record_usage", lambda p, pr, co: recorded.append((pr, co)))

    gate1 = UsageGate([(S_PLAN, 100, 10), (S_OUTLINE, 200, 20), RuntimeError("炸")])
    install_stage_context(monkeypatch, gate1)
    p, novel, chapter, job, db = world
    with pytest.raises(RuntimeError):
        asyncio.run(cs.run_staged_chapter(p, novel, job, chapter, 7, db))
    first = list(recorded)

    gate2 = UsageGate([(S_WRITE, 300, 30), (S_REVIEW, 400, 40), (S_REVISE, 500, 50)])
    install_stage_context(monkeypatch, gate2)
    result, _ = run_staged(world, gate=gate2)
    second = recorded[len(first) :]

    assert first == [(300, 30)], f"第一次应只记已花的 300/30，实得 {first}"
    assert sum(v[0] for v in second) > 0, "续跑段也没记账 → 重入完全免费，假免费"
    assert gate2.attempted == ["写作", "审校", "修订"], f"重入必须从第 3 段续，实得 {gate2.attempted}"
    assert result is not None
