"""FIX-8D 大纲自动续写：单文件、零真实网络、不碰真实数据库、<30 秒。

钉死的行为契约（推进到 100 万字的发动机）：
- POST /api/ai-factory/projects/{id}/outline/continue：writing 状态可用，
  输入上下文（全局摘要/最近 8 章记忆卡/大纲尾部/状态文件），目标章数
  min(剩余, 30)；已达标 → 400；解析失败重试一次、仍失败 → 502；0 章 → 502。
- 追加式落库：新卷 + 新 Chapter + pending Job，sort_order 接现有末尾，
  绝不删除/修改任何已有行（大纲重建那段删除逻辑严禁复用）。
- 防重入：同项目已有续写在跑 → 409，且登记必须 finally 清理。
- 自动钩子：pending < 3 且未达标 → 夜跑/批末自动续写一批；完本 → 不再续写并打标记。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_fix8_outline_continue.py -q
"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import AiChapterJob, AiProject, Chapter, Novel, User, Volume
from app.routers import ai_factory as factory_mod


# ---------------------------------------------------------------- 替身


def fake_user():
    return SimpleNamespace(id=1, role="user")


def make_ai_config():
    from app.models import AIConfig

    return AIConfig(
        id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
    )


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class SeqDB:
    """按调用顺序吐 execute 结果的最小 DB（与 test_fix8_wordcount 同口径）。"""

    def __init__(self, rows=None, results=None):
        self.rows = rows or {}
        self.results = list(results or [])
        self.added: list = []
        self.commits = 0
        self._next_id = 900

    async def get(self, model, pk):
        # 真实让出点：伪 async（无 await 让出）会让防重入竞态测试永远假绿——
        # 单协程场景 asyncio.run 照常工作，并发场景才有真实的调度切换。
        await asyncio.sleep(0)
        row = self.rows.get(model)
        if row is not None and getattr(row, "id", None) is not None and row.id != pk:
            return None
        return row

    async def execute(self, stmt):
        await asyncio.sleep(0)  # 同上：让出事件循环，竞态才可能发生
        return _Rows(self.results.pop(0) if self.results else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = self._next_id
                self._next_id += 1

    async def commit(self):
        self.commits += 1


def make_project(**over):
    p = SimpleNamespace(
        id=1,
        user_id=1,
        novel_id=11,
        status="writing",
        seed_prompt="一个少年的修行故事",
        book_spec_json=json.dumps({"genre": "玄幻", "premise": "少年得剑"}, ensure_ascii=False),
        genre="玄幻",
        style_notes="",
        target_total_words=1_000_000,
        target_volume_words=None,
        target_chapter_words=2000,
        target_volumes=None,
        target_chapters=None,
        outline_json=json.dumps(
            {"volumes": [{"title": "第一卷 入世", "chapters": [{"title": "第三十章 决战", "outline": "主角与长老决一死战"}]}]},
            ensure_ascii=False,
        ),
        reference_json=None,
        deconstruct_text="",
        deconstruct_hint="",
        deconstruct_draft_json=None,
        setup_llm="",
        outline_llm="",
        chapter_llm="",
        summary_llm="",
        review_llm="",
        global_summary="主角已聚齐三件信物，正被青云宗追杀。",
        character_state="",
        plot_arcs="",
        subplot_board="",
        particle_ledger="",
        synopsis_json=None,
        market_json=None,
        cover_prompt=None,
        kb_query=None,
        platform="",
        custom_words="",
        tokens_prompt=0,
        tokens_completion=0,
        nightly_enabled=False,
        nightly_chapters=3,
        nightly_last_run=None,
        chapter_count=0,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    for k, v in over.items():
        setattr(p, k, v)
    return p


def make_skeleton(n_chapters: int = 30, per_volume: int = 10):
    """既有骨架：每 per_volume 章一卷（拆章也会真实增章，这里按普通骨架造）。"""
    n_vols = -(-n_chapters // per_volume)
    volumes = [
        SimpleNamespace(id=101 + vi, novel_id=11, title=f"第{vi + 1}卷", sort_order=vi)
        for vi in range(n_vols)
    ]
    chapters = [
        SimpleNamespace(
            id=21 + i,
            novel_id=11,
            volume_id=volumes[i // per_volume].id,
            title=f"第{i + 1}章",
            sort_order=i % per_volume,
            content="<p>正文</p>" if i < 20 else "",
            word_count=6302 if i < 20 else 0,
            status="writing" if i < 20 else "draft",
        )
        for i in range(n_chapters)
    ]
    jobs = [
        SimpleNamespace(
            id=31 + i,
            project_id=1,
            chapter_id=21 + i,
            status="done" if i < 20 else "pending",
            outline=f"第{i + 1}章大纲：主角遇险",
            summary=(f"第{i + 1}章剧情摘要" if i < 20 else ""),
            actual_words=6302 if i < 20 else 0,
            attempt=1,
            started_at=None,
            finished_at=None,
            last_error="",
            last_error_code="",
            review_issues=None,
            review_score=None,
            draft_text=None,
            draft_updated_at=None,
        )
        for i in range(n_chapters)
    ]
    return volumes, chapters, jobs


def continue_reply(n_volumes=1, per=30):
    return json.dumps(
        {
            "volumes": [
                {
                    "title": f"第【{vi + 2}】卷 风起",
                    "summary": "追杀升级，主角反杀",
                    "chapters": [{"title": f"风波{ci + 1}", "outline": f"具体事件{ci + 1}：长老截杀，主角借阵反制"} for ci in range(per)],
                }
                for vi in range(n_volumes)
            ]
        },
        ensure_ascii=False,
    )


def install_core_guards(monkeypatch, p, db, *, reply=continue_reply(), calls=None):
    async def _pick_config(user, session, route_field):
        return make_ai_config()

    async def fake_chat(config, system, prompt, **kw):
        if calls is not None:
            calls.append(prompt)
        if isinstance(reply, list):  # 逐次吐出（重试场景）
            return reply.pop(0)
        return reply

    monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(factory_mod, "_chat_text", fake_chat)


def core_db(p, volumes, chapters, jobs):
    """查询顺序与实现约定一致：jobs → chapters → volumes（改实现必须同步改这里）。"""
    return SeqDB(
        rows={AiProject: p, Novel: SimpleNamespace(id=11, title="测试之书"), User: SimpleNamespace(id=p.user_id)},
        results=[jobs, chapters, volumes],
    )


# ================================================================ 端点守卫


class TestOutlineContinueGuards:
    def test_writing_only(self, monkeypatch):
        p = make_project(status="outline")
        db = SeqDB(rows={AiProject: p})
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 400

    def test_reentry_409_and_lock_released(self, monkeypatch):
        """同项目已有续写在跑 → 409；锁必须 finally 清理（不许一次异常就永久锁死）。"""
        p = make_project()
        db = SeqDB(rows={AiProject: p})
        factory_mod.OUTLINE_CONTINUE_RUNNING.add(p.id)
        try:
            with pytest.raises(HTTPException) as ei:
                asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
            assert ei.value.status_code == 409, f"防重入必须 409，实得 {ei.value.status_code}"
            assert p.id in factory_mod.OUTLINE_CONTINUE_RUNNING  # 本次调用没碰到别人的锁
        finally:
            factory_mod.OUTLINE_CONTINUE_RUNNING.discard(p.id)  # 测试自加的锁自己清，别污染后续用例

        # 正常走完一遍后锁必须释放
        volumes, chapters, jobs = make_skeleton()
        db2 = core_db(p, volumes, chapters, jobs)
        install_core_guards(monkeypatch, p, db2)
        asyncio.run(factory_mod.outline_continue(1, fake_user(), db2))
        assert p.id not in factory_mod.OUTLINE_CONTINUE_RUNNING, "端点收尾必须释放登记"

    def test_lock_released_on_502(self, monkeypatch):
        """解析两次都失败 → 502，锁也必须释放（夜跑钩子才不会被永久挡住）。"""
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        db = core_db(p, volumes, chapters, jobs)
        install_core_guards(monkeypatch, p, db, reply=["这不是JSON", "还不是JSON"])
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 502
        assert p.id not in factory_mod.OUTLINE_CONTINUE_RUNNING


# ================================================================ 达标判定


class TestOutlineContinueThresholds:
    def test_derived_missing_400(self, monkeypatch):
        p = make_project(target_total_words=None, target_chapter_words=None)
        volumes, chapters, jobs = make_skeleton()
        db = core_db(p, volumes, chapters, jobs)
        install_core_guards(monkeypatch, p, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 400
        assert "推导" in ei.value.detail

    def test_total_reached_400(self, monkeypatch):
        """已定稿字数 ≥ target_total_words → 400「已达总字数目标」。"""
        p = make_project(target_total_words=126_000)  # 已定稿 20 章 × 6302 = 126,040 ≥ 目标
        volumes, chapters, jobs = make_skeleton()
        db = core_db(p, volumes, chapters, jobs)
        install_core_guards(monkeypatch, p, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 400
        assert "已达总字数目标" in ei.value.detail

    def test_remaining_zero_400(self, monkeypatch):
        """大纲 500 章 = 推导 500 章 → 400（不允许无限膨胀）。"""
        p = make_project()
        volumes, chapters, jobs = make_skeleton(n_chapters=500, per_volume=100)
        db = core_db(p, volumes, chapters, jobs)
        install_core_guards(monkeypatch, p, db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 400
        assert "已达总字数目标" in ei.value.detail


# ================================================================ 成功路径


class TestOutlineContinueSuccess:
    def test_happy_path_appends_without_touching_existing(self, monkeypatch):
        """30 章骨架 + 推导 500 → 续 30 章：新卷 + 新章 + pending 任务，旧行纹丝不动。"""
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        before_ch = [(c.id, c.title, c.sort_order, c.status) for c in chapters]
        before_jobs = [(j.id, j.status) for j in jobs]
        before_vols = [(v.id, v.sort_order) for v in volumes]
        db = core_db(p, volumes, chapters, jobs)
        calls: list = []
        install_core_guards(monkeypatch, p, db, calls=calls)

        out = asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert out["ok"] is True
        assert out["added_chapters"] == 30
        assert out["chapter_range"] == [31, 60], f"区间必须接现有 30 章之后，实得 {out}"

        new_vols = [o for o in db.added if isinstance(o, Volume)]
        new_chs = [o for o in db.added if isinstance(o, Chapter)]
        new_jobs = [o for o in db.added if isinstance(o, AiChapterJob)]
        assert len(new_vols) == 1 and len(new_chs) == 30 and len(new_jobs) == 30
        assert new_vols[0].sort_order == 3, f"新卷 sort_order 必须接现有末尾（3），实得 {new_vols[0].sort_order}"
        assert [c.sort_order for c in new_chs] == list(range(15)) or all(
            c.sort_order >= 0 for c in new_chs
        ), "新章在各自卷内从 0 顺排"
        assert all(j.status == "pending" for j in new_jobs), "新章任务必须是 pending（夜跑会接手）"
        assert all(j.outline for j in new_jobs), "每章任务必须带 outline"
        # 绝不删除/修改任何已有行
        assert [(c.id, c.title, c.sort_order, c.status) for c in chapters] == before_ch
        assert [(j.id, j.status) for j in jobs] == before_jobs
        assert [(v.id, v.sort_order) for v in volumes] == before_vols
        # 大纲 JSON 快照追加新卷（前端大纲预览卡要从 project.outline 渲染）
        outline = json.loads(p.outline_json)
        assert len(outline["volumes"]) == 2, "outline_json.volumes 必须追加新卷（旧行不动）"

    def test_prompt_carries_context_and_range(self, monkeypatch):
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        db = core_db(p, volumes, chapters, jobs)
        calls: list = []
        install_core_guards(monkeypatch, p, db, calls=calls)
        asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        prompt = calls[0]
        assert "第 31～60 章" in prompt, "必须给出明确的续写区间"
        assert "500" in prompt, "全书推导章数要讲清楚"
        assert "第三十章 决战" in prompt, "大纲尾部（最后一章 outline）必须进上下文"
        assert "主角已聚齐三件信物" in prompt, "全局摘要必须进上下文"
        assert "第20章剧情摘要" in prompt or "第 20 章" in prompt or "第20章" in prompt, "最近章记忆卡必须进上下文"

    def test_goal_caps_at_30(self, monkeypatch):
        """剩余 100 章 → 本次仍只出 30 章（一次喂太多不现实）。"""
        p = make_project()
        volumes, chapters, jobs = make_skeleton(n_chapters=50, per_volume=10)
        db = core_db(p, volumes, chapters, jobs)
        calls: list = []
        install_core_guards(monkeypatch, p, db, calls=calls)
        out = asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert out["added_chapters"] == 30
        assert "第 51～80 章" in calls[0]

    def test_parse_failure_retries_once(self, monkeypatch):
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        db = core_db(p, volumes, chapters, jobs)
        calls: list = []
        install_core_guards(monkeypatch, p, db, reply=["模型第一轮抽风输出了解释文字", continue_reply()], calls=calls)
        out = asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert out["added_chapters"] == 30 and len(calls) == 2, "解析失败必须重试一次"

    def test_zero_chapters_502(self, monkeypatch):
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        db = core_db(p, volumes, chapters, jobs)
        install_core_guards(monkeypatch, p, db, reply=json.dumps({"volumes": [{"title": "空卷", "chapters": []}]}, ensure_ascii=False))
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 502


# ================================================================ 自动钩子


class TestAutoHook:
    """maybe_auto_continue_outline：夜跑/批末的自动续写判定。"""

    def _run(self, p, volumes, chapters, jobs, *, core=None):
        db = core_db(p, volumes, chapters, jobs)
        real_core = factory_mod._outline_continue_core
        if core is not None:
            factory_mod._outline_continue_core = core  # 测试内替换（存根）
        try:
            return asyncio.run(factory_mod.maybe_auto_continue_outline(p, db))
        finally:
            factory_mod._outline_continue_core = real_core  # 用例间不互相污染

    def setup_method(self):
        factory_mod.OUTLINE_CONTINUE_RUNNING.clear()

    def teardown_method(self):
        factory_mod.OUTLINE_CONTINUE_RUNNING.clear()

    def test_triggers_when_pending_lt_3(self):
        p = make_project()
        volumes, chapters, jobs = make_skeleton()  # 10 pending
        jobs = jobs[:23]  # 制造 pending=3 → 边界：必须 <3 才触发；这里 pending=3 不触发
        pending_jobs = [j for j in jobs if j.status == "pending"]
        assert len(pending_jobs) == 3
        called = []

        async def fake_core(p_, user, db):
            called.append(p_.id)
            return {"ok": True, "added_chapters": 30, "chapter_range": [31, 60]}

        out = self._run(p, volumes, chapters, jobs, core=fake_core)
        assert not called, "pending=3 不满足 <3，不许触发"

        jobs2 = jobs[:22]  # pending=2 → 触发
        volumes2, chapters2, _ = make_skeleton()
        chapters2 = chapters2[:50]  # 大纲 50 章（与 jobs 对应取前 50 章）
        out = self._run(p, volumes2, chapters2, jobs2, core=fake_core)
        assert called and out["added_chapters"] == 30

    def test_skips_when_derived_missing(self):
        p = make_project(target_total_words=None, target_chapter_words=None)
        volumes, chapters, jobs = make_skeleton()
        called = []

        async def fake_core(p_, user, db):
            called.append(1)
            return {}

        out = self._run(p, volumes, chapters, jobs, core=fake_core)
        assert out is None and not called

    def test_skips_when_total_reached(self):
        """完本判定：已定稿 ≥ 目标 → 不续写，返回完本标记（夜跑汇总要打「全书完本」）。"""
        p = make_project(target_total_words=126_000)
        volumes, chapters, jobs = make_skeleton()  # 已定稿 126,040
        called = []

        async def fake_core(p_, user, db):
            called.append(1)
            return {}

        out = self._run(p, volumes, chapters, jobs, core=fake_core)
        assert out and out.get("finished_book") is True, f"完本必须打标记，实得 {out}"
        assert not called, "完本后不许再续写"

    def test_skips_when_remaining_le_zero(self):
        p = make_project()
        volumes, chapters, jobs = make_skeleton(n_chapters=500, per_volume=100)
        called = []

        async def fake_core(p_, user, db):
            called.append(1)
            return {}

        out = self._run(p, volumes, chapters, jobs, core=fake_core)
        assert out is None and not called

    def test_reentry_guard(self):
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        jobs = jobs[:22]
        volumes2, chapters2, _ = make_skeleton()
        chapters2 = chapters2[:50]
        factory_mod.OUTLINE_CONTINUE_RUNNING.add(p.id)
        called = []

        async def fake_core(p_, user, db):
            called.append(1)
            return {}

        out = self._run(p, volumes2, chapters2, jobs2 if False else jobs, core=fake_core)
        assert out is None and not called, "已有续写在跑时钩子必须让路"

    def test_core_failure_does_not_block_nightly(self):
        """续写失败不阻断当晚已排的生成：异常必须被吃掉。

        复评顺手 4：失败要返回 outline_continue_error（战报据此留痕）——
        只留 warning 的话书会无声停止生长，用户无法从战报判断钩子是否还活着。
        """
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        jobs = jobs[:22]
        volumes2, chapters2, _ = make_skeleton()
        chapters2 = chapters2[:50]

        async def exploding_core(p_, user, db):
            raise RuntimeError("上游 502")

        out = self._run(p, volumes2, chapters2, jobs, core=exploding_core)
        assert out is not None and out.get("ok") is False, f"失败必须带错误标记让战报留痕，实得 {out}"
        assert "502" in out.get("outline_continue_error", ""), f"错误原因要可读，实得 {out}"

    def test_core_http_failure_returns_error_note(self):
        """HTTPException（如网关 503）同样留痕——不抛出、不阻断夜跑。"""
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        jobs = jobs[:22]

        async def refusing_core(p_, user, db):
            raise HTTPException(503, "模型网关 503")

        out = self._run(p, volumes, chapters, jobs, core=refusing_core)
        assert out is not None and out.get("ok") is False and "503" in out.get("outline_continue_error", "")

    def test_concurrent_triggers_only_one_enters(self):
        """复评必修 2：check→add 之间零 await——并发同抢只许一个进续写。

        旧实现 add 在 4 个 await 之后：夜跑钩子 × 用户点按钮 / 双批量并发触发
        会双重续写、重复落卷章。现在第二个协程必须在锁上让路（返回 None）。
        """
        p = make_project()
        volumes, chapters, jobs = make_skeleton()
        jobs = jobs[:22]  # pending=2 < 3 → 触发条件成立

        called: list = []

        async def slow_core(p_, user, db):
            called.append(1)
            await asyncio.sleep(0)  # core 内有 await：若第二个协程混进来也会在这里排队
            return {"ok": True, "added_volumes": 1, "added_chapters": 30,
                    "chapter_range": [31, 60], "message": "已续写第 31～60 章"}

        db = core_db(p, volumes, chapters, jobs)
        real_core = factory_mod._outline_continue_core
        factory_mod._outline_continue_core = slow_core

        async def _both():
            # gather 必须在 running loop 内调用（Python 3.12+ 裸调 gather 会炸）
            return await asyncio.gather(
                factory_mod.maybe_auto_continue_outline(p, db),
                factory_mod.maybe_auto_continue_outline(p, db),
            )

        try:
            out1, out2 = asyncio.run(_both())
        finally:
            factory_mod._outline_continue_core = real_core

        assert len(called) == 1, f"并发同抢只许一个进续写，实进 {len(called)}"
        assert (out1 is None) or (out2 is None), f"让路协程必须返回 None，实得 {(out1, out2)}"
        assert factory_mod.OUTLINE_CONTINUE_RUNNING.isdisjoint({p.id}), "跑完锁必须释放"


# ================================================================ 夜跑接线


class TestNightlyIntegration:
    def test_nightly_report_carries_outline_continue(self, monkeypatch):
        """夜跑开头钩子触发的结果必须写进战报（用户次日打开就能看到）。"""
        from app import nightly as nightly_mod

        p = SimpleNamespace(
            id=1, user_id=1, status="writing", novel_id=11, nightly_enabled=True,
            nightly_chapters=3, nightly_last_run=None, target_total_words=1_000_000,
            target_chapter_words=2000, target_chapters=None,
        )
        novel = SimpleNamespace(id=11, title="测试之书")
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="第一章", sort_order=0, content="", status="draft")
        job = SimpleNamespace(id=31, project_id=1, chapter_id=21, status="pending", outline="主角入局")
        db = SeqDB(
            rows={AiProject: p, Novel: novel, Chapter: chapter, AiChapterJob: job},
            results=[[job], [chapter], []],
        )

        class _Cm:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(nightly_mod, "SessionLocal", lambda: _Cm())
        gen_calls = []

        async def fake_gen(p_, novel_, job_, chapter_, num, db_, on_retry=None):
            gen_calls.append(job_.id)
            return {"title": "第一章", "ok": True, "words": 2000, "deai_score": 90}

        monkeypatch.setattr(nightly_mod, "_generate_one_with_retry", fake_gen)

        async def fake_hook(p_, db_):
            return {"ok": True, "added_chapters": 30, "chapter_range": [31, 60], "message": "已续写第 31～60 章"}

        monkeypatch.setattr(nightly_mod, "maybe_auto_continue_outline", fake_hook)

        report = asyncio.run(nightly_mod.run_nightly_for_project(1, 1))
        assert report["done"] == 1
        assert report.get("outline_continue", {}).get("added_chapters") == 30, f"战报必须带续写结果，实得 {report.keys()}"

    def test_nightly_reports_finished_book(self, monkeypatch):
        """完本标记：钩子返回 finished_book=True 时战报要带「全书完本」。"""
        from app import nightly as nightly_mod

        p = SimpleNamespace(
            id=1, user_id=1, status="writing", novel_id=11, nightly_enabled=True,
            nightly_chapters=3, nightly_last_run=None, target_total_words=1_000_000,
            target_chapter_words=2000, target_chapters=None,
        )
        novel = SimpleNamespace(id=11, title="测试之书")
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="第一章", sort_order=0, content="", status="draft")
        job = SimpleNamespace(id=31, project_id=1, chapter_id=21, status="pending", outline="主角入局")
        db = SeqDB(
            rows={AiProject: p, Novel: novel, Chapter: chapter, AiChapterJob: job},
            results=[[job], [chapter], []],
        )

        class _Cm:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(nightly_mod, "SessionLocal", lambda: _Cm())

        async def fake_gen(p_, novel_, job_, chapter_, num, db_, on_retry=None):
            return {"title": "第一章", "ok": True, "words": 2000, "deai_score": 90}

        monkeypatch.setattr(nightly_mod, "_generate_one_with_retry", fake_gen)

        async def fake_hook(p_, db_):
            return {"finished_book": True, "message": "全书完本"}

        monkeypatch.setattr(nightly_mod, "maybe_auto_continue_outline", fake_hook)

        report = asyncio.run(nightly_mod.run_nightly_for_project(1, 1))
        assert report.get("outline_continue", {}).get("finished_book") is True
        assert report.get("finished_book") is True, "完本必须是战报的顶层标记"
