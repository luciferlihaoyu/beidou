"""技能卡注入可观测性（t3-wire-observe）回归测试。

被钉死的行为契约（TASK-t3-wire-observe §3.6）：

- 两张表各多一列 `skill_wire_json`（项目级 D1 按节点归并 / 章级 D2 列表原样），
  老库缺列时 `init_db` 能幂等补上；
- 项目级字典：结构化节点跑一次就多一个键，值与 `skillwire.blank_record` 8 键同构；
  同节点重跑 → **覆盖**而非追加；
- 章级：UI / 夜跑 / 批量**三条正文路径**各自落库（D3 证据，不许只在 UI 路径生效）；
- 观测写入**绝不抛**：json.dumps 被炸掉时，立项端点与夜跑主流程照常成功；
- 查询端点 `GET /api/ai-factory/projects/{id}/skill-wire`：
  正常返回 / 404 / 数据脏降级 `{"_raw_error": ...}` 不 500；
- 护栏：`skill_wire_json` 里只有计数与卡名，**绝不出现 prompt / 正文**
  （否则几百章的 prompt 全堆进项目表）。

单文件、零真实网络、不碰真实库、<30 秒。
运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python -m pytest tests/test_wire_observe.py -q
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as app_db
import app.nightly as nightly_mod
import app.routers.ai_factory as factory_mod
import app.routers.ai_extras as extras_mod
import app.routers.batch as batch_mod
import app.routers.skills as skills_mod
from app import skillwire
from app.models import (
    AIConfig,
    AiChapterJob,
    AiProject,
    Base,
    Chapter,
    Novel,
    User,
)

APP = Path(__file__).resolve().parents[1] / "app"

#: 护栏哨兵：混进 prompt / 正文的标记串，绝不允许出现在 skill_wire_json 里
SENTINEL = "哨兵正文-QQ7-不得进入观测表"

#: 章级三条路径共用的装配查询序列（Character / Chapter / Volume / done_jobs / Integration / novel_texts）
ASSEMBLE_RESULTS = "[chapter], [], [], [], [], []"

WIRE_TRIM_MARK = "…（观测记录过长，notes 已截断）"
WIRE_LIMIT = 8000


# ---------------------------------------------------------------- 通用替身


def fake_user():
    return SimpleNamespace(id=1, role="user")


def make_ai_config() -> AIConfig:
    return AIConfig(
        id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
    )


class _Rows:
    """db.execute(...) 结果的最小形态：.scalars().all()。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class SeqDB:
    """按调用顺序吐 execute 结果、按模型给 get 行的最小 DB（与 test_fix8_wordcount 同款）。"""

    def __init__(self, rows=None, results=None):
        self.rows = rows or {}
        self.results = list(results or [])
        self.commits = 0

    async def get(self, model, pk):
        row = self.rows.get(model)
        if row is not None and getattr(row, "id", None) is not None and row.id != pk:
            return None
        return row

    async def execute(self, stmt):
        return _Rows(self.results.pop(0) if self.results else [])

    def add(self, obj):
        pass

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _session_factory(db):
    """把现成 DB 伪装成自持会话的 SessionLocal。"""

    class _Cm:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    return lambda: _Cm()


def _patch_upstream(monkeypatch, text: str):
    """OpenAI 兼容 SSE 上游桩（零真实网络）。"""
    real = httpx.AsyncClient

    def handler(request):
        async def stream():
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=stream())

    def factory(*args, **kwargs):
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _assemble_db(chapter):
    """给真实 _assemble_context 用的 SeqDB：6 次查询按序吐（角色/章节/卷/记忆卡/集成/正文语料）。"""
    return SeqDB(results=[[], [chapter], [], [], [], []])


def _fake_chapter_job(**over):
    job = SimpleNamespace(
        id=7,
        project_id=1,
        chapter_id=21,
        status="pending",
        attempt=0,
        started_at=None,
        last_error="",
        last_error_code="",
        outline="主角入局",
        summary="",
        actual_words=0,
        finished_at=None,
        skill_wire_json="",
    )
    for k, v in over.items():
        setattr(job, k, v)
    return job


def _fake_chapter(**over):
    chapter = SimpleNamespace(
        id=21,
        novel_id=11,
        volume_id=None,
        title="第一章 开局",
        sort_order=0,
        content="",
        word_count=0,
        status="draft",
    )
    for k, v in over.items():
        setattr(chapter, k, v)
    return chapter


def _fake_novel():
    return SimpleNamespace(id=11, title="测试之书")


def _fake_project(**over):
    p = SimpleNamespace(
        id=1,
        user_id=1,
        status="writing",
        novel_id=11,
        chapter_llm=None,
        summary_llm=None,
        book_spec_json="",
        style_notes="",
        global_summary="",
        character_state="",
        plot_arcs="",
        kb_query=None,
        context_recent_chapters=2,
        context_extra_chapters="[]",
        target_chapter_words=None,
        author_intent="",
        current_focus="",
        tokens_prompt=0,
        tokens_completion=0,
    )
    for k, v in over.items():
        setattr(p, k, v)
    return p


# ---------------------------------------------------------------- 内存库夹具


@pytest_asyncio.fixture
async def sl():
    """独立内存库（Base.metadata.create_all 直接带新列）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def make_project(sl):
    async with sl() as db:
        user = User(username="t3-tester", password_hash="x")
        db.add(user)
        await db.flush()
        p = AiProject(user_id=user.id, seed_prompt="少年携剑入局的修行故事")
        db.add(p)
        await db.commit()
        return user, p


# ---------------------------------------------------------------- 假 LLM（结构化节点用）


_INIT_JSON = json.dumps(
    {
        "titles": ["剑起青萍", "青萍剑歌", "一剑破空"],
        "genre": "玄幻",
        "time": "架空古代",
        "place": "青萍山",
        "theme": "逆袭",
        "tone": "热血",
        "pov": "第三人称限制视角-主角",
        "characters": [{"name": "林青云", "role": "主角", "brief": "外门弟子"}],
        "premise": "少年携残剑逆袭，最大阻碍是宗门倾轧。",
    },
    ensure_ascii=False,
)
_SETUP_JSON = json.dumps(
    {
        "characters": [
            {"name": "林青云", "role": "主角", "description": "坚韧外门弟子", "arc": "从外门杀回核心"},
            {"name": "赵无极", "role": "反派", "description": "内门首席", "arc": "压制主角"},
        ],
        "worldview": [
            {"category": "力量体系", "title": "炼气九层", "content": "每层需一枚淬体丹"},
            {"category": "地理", "title": "青萍山", "content": "分外门内门核心三区"},
        ],
    },
    ensure_ascii=False,
)
_VOLUMES_JSON = json.dumps(
    {
        "volumes": [
            {
                "title": "第一卷 外门",
                "summary": "外门求生",
                "chapters": [
                    {"title": "残剑出世", "outline": "林青云捡到残剑，被赵无极盯上"},
                    {"title": "夜探藏经阁", "outline": "偷学剑诀，险被擒"},
                ],
            }
        ]
    },
    ensure_ascii=False,
)
_SYNOPSIS_JSON = json.dumps(
    {"short": "少年以残剑逆袭。", "standard": "外门弟子林青云捡到残剑，自此一路逆袭。", "promotion": "看外门弃徒如何一剑破局！", "douyin": "他捡的剑会认主。"},
    ensure_ascii=False,
)


def install_llm(monkeypatch):
    """假 _pick_config/_chat_text：按 prompt 指纹分发预设 JSON（结构化节点全走真实接线）。"""
    calls: list[str] = []

    async def fake_pick(user, session, route_field):
        return make_ai_config()

    async def fake_chat(config, system, prompt, **kw):
        calls.append(prompt)
        if '"douyin"' in prompt:
            return _SYNOPSIS_JSON
        if "【待审校章节" in prompt:
            return "[]"
        if '"characters"' in prompt and '"worldview"' in prompt:
            return _SETUP_JSON
        if '"volumes"' in prompt:
            return _VOLUMES_JSON
        if '"titles"' in prompt:
            return _INIT_JSON
        raise AssertionError("假 LLM 不认识这段 prompt：" + prompt[:120])

    for mod in (factory_mod, extras_mod):
        monkeypatch.setattr(mod, "_chat_text", fake_chat)
        monkeypatch.setattr(mod, "_pick_config", fake_pick)
    return calls


# ================================================================ 列与迁移


class TestColumnsAndMigration:
    @pytest.mark.asyncio
    async def test_new_rows_default_to_empty_string(self, sl):
        """两张表都有 skill_wire_json 列，新行默认空串（D1/D2 列名钉死）。"""
        user, p = await make_project(sl)
        assert p.skill_wire_json == ""
        async with sl() as db:
            nv = Novel(user_id=user.id, title="书")
            db.add(nv)
            await db.flush()
            job = AiChapterJob(project_id=p.id, chapter_id=None, status="pending")
            db.add(job)
            await db.commit()
            assert job.skill_wire_json == ""

    @pytest.mark.asyncio
    async def test_ensure_column_adds_both_columns_idempotent(self, tmp_path):
        """缺列老库被 _ensure_column 补上，重复调用不抛（迁移原语层）。"""
        from app.db import _ensure_column

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE ai_projects DROP COLUMN skill_wire_json"))
            await conn.execute(text("ALTER TABLE ai_chapter_jobs DROP COLUMN skill_wire_json"))

        async with engine.begin() as conn:
            await _ensure_column(conn, "ai_projects", "skill_wire_json", "TEXT NOT NULL DEFAULT ''")
            await _ensure_column(conn, "ai_chapter_jobs", "skill_wire_json", "TEXT NOT NULL DEFAULT ''")
            # 幂等：再跑一轮不许抛
            await _ensure_column(conn, "ai_projects", "skill_wire_json", "TEXT NOT NULL DEFAULT ''")
            await _ensure_column(conn, "ai_chapter_jobs", "skill_wire_json", "TEXT NOT NULL DEFAULT ''")

        async with engine.connect() as conn:
            cols_p = [r[1] for r in (await conn.execute(text("PRAGMA table_info(ai_projects)"))).fetchall()]
            cols_j = [r[1] for r in (await conn.execute(text("PRAGMA table_info(ai_chapter_jobs)"))).fetchall()]
        await engine.dispose()
        assert "skill_wire_json" in cols_p
        assert "skill_wire_json" in cols_j

    @pytest.mark.asyncio
    async def test_init_db_repairs_legacy_db(self, tmp_path, monkeypatch):
        """端到端迁移证据：手工造出缺列旧库（含旧数据）→ init_db 补列且旧行默认 ''。

        造旧库的方式：ORM 插入合法行（所有 default 齐全）→ DROP COLUMN 模拟
        「该列尚未迁移上去的旧版本表结构」。裸 SQL 追 NOT NULL 列没完没了，ORM 才是正道。
        """
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as s:
            s.add(User(id=1, username="legacy", password_hash="x", role="admin"))
            s.add(AiProject(id=1, user_id=1, seed_prompt="旧项目", status="draft"))
            s.add(AiChapterJob(id=1, project_id=1, status="pending"))
            await s.commit()
        async with engine.begin() as conn:
            # 复现旧版本：列还不存在（数据行已在）
            await conn.execute(text("ALTER TABLE ai_projects DROP COLUMN skill_wire_json"))
            await conn.execute(text("ALTER TABLE ai_chapter_jobs DROP COLUMN skill_wire_json"))
            cols_p = [r[1] for r in (await conn.execute(text("PRAGMA table_info(ai_projects)"))).fetchall()]
            cols_j = [r[1] for r in (await conn.execute(text("PRAGMA table_info(ai_chapter_jobs)"))).fetchall()]
        assert "skill_wire_json" not in cols_p and "skill_wire_json" not in cols_j

        monkeypatch.setattr(app_db, "engine", engine)
        monkeypatch.setattr(app_db, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))
        await app_db.init_db()

        async with engine.connect() as conn:
            cols_p = [r[1] for r in (await conn.execute(text("PRAGMA table_info(ai_projects)"))).fetchall()]
            cols_j = [r[1] for r in (await conn.execute(text("PRAGMA table_info(ai_chapter_jobs)"))).fetchall()]
        assert "skill_wire_json" in cols_p, "init_db 没给 ai_projects 补列"
        assert "skill_wire_json" in cols_j, "init_db 没给 ai_chapter_jobs 补列"

        # 旧行还在，且新列按默认值可读
        async with app_db.SessionLocal() as s:
            old_p = await s.get(AiProject, 1)
            old_j = await s.get(AiChapterJob, 1)
            assert old_p is not None and old_p.seed_prompt == "旧项目" and old_p.skill_wire_json == ""
            assert old_j is not None and old_j.status == "pending" and old_j.skill_wire_json == ""
        await engine.dispose()


# ================================================================ 项目级归并（D1）


class TestProjectWireMerge:
    def test_same_node_rerun_overwrites_not_appends(self):
        """同节点重跑 → 覆盖该键（可观测的是「这次注了什么」，不是审计流水）。"""
        p = SimpleNamespace(skill_wire_json="")
        r1 = skillwire.blank_record("init")
        r1["notes"] = ["第一跑"]
        r2 = skillwire.blank_record("init")
        r2["notes"] = ["第二跑"]
        factory_mod._save_project_wire(p, "init", r1)
        factory_mod._save_project_wire(p, "init", r2)
        data = json.loads(p.skill_wire_json)
        assert list(data.keys()) == ["init"], f"同节点必须覆盖而非追加：{list(data.keys())}"
        assert data["init"]["notes"] == ["第二跑"], "保留的必须是最新一次的 record"

    def test_corrupt_old_value_rebuilds_with_trace(self):
        """旧值 JSON 解析失败 → 用 {} 重建并留痕，绝不抛。"""
        p = SimpleNamespace(skill_wire_json="{这不是合法JSON")
        rec = skillwire.blank_record("setup")
        factory_mod._save_project_wire(p, "setup", rec)
        data = json.loads(p.skill_wire_json)
        assert set(data.keys()) == {"setup"}
        assert any("解析失败" in n or "重建" in n for n in data["setup"]["notes"]), data["setup"]["notes"]

    def test_empty_record_is_a_noop(self):
        """record 缺席（None/空）不许把已有观测洗掉。"""
        p = SimpleNamespace(skill_wire_json='{"init": {"node": "init"}}')
        factory_mod._save_project_wire(p, "setup", None)  # type: ignore[arg-type]
        assert json.loads(p.skill_wire_json) == {"init": {"node": "init"}}

    def test_oversize_notes_trimmed_with_mark(self):
        """整体超 ~8000 字 → 截 notes 并加留痕标记；卡名/计数（可观测主体）不裁。"""
        rec = skillwire.blank_record("outline")
        rec["notes"] = ["啰嗦的日志条目" * 300] * 80  # ≈ 14 万字
        rec["cards"] = [
            {
                "slug": "outline-architect",
                "chars": 1234,
                "tool_chars": 0,
                "manual_chars": 0,
                "loaded": [{"rel": "references/outline-framework.md", "chars": 800}],
                "notes": ["卡内说明" * 100] * 40,
            }
        ]
        p = SimpleNamespace(skill_wire_json="")
        factory_mod._save_project_wire(p, "outline", rec)
        stored = p.skill_wire_json
        assert len(stored) <= WIRE_LIMIT, f"截断后仍超限：{len(stored)}"
        data = json.loads(stored)
        assert WIRE_TRIM_MARK in data["outline"]["notes"], "必须留下截断标记"
        card = data["outline"]["cards"][0]
        assert card["slug"] == "outline-architect" and card["chars"] == 1234, "卡名/计数不许被裁"

    def test_within_limit_notes_untouched(self):
        """没超限就原样存，一个字都不动（截断逻辑不许误伤小记录）。"""
        rec = skillwire.blank_record("outline")
        rec["notes"] = ["正常长度的一条说明"]
        p = SimpleNamespace(skill_wire_json="")
        factory_mod._save_project_wire(p, "outline", rec)
        data = json.loads(p.skill_wire_json)
        assert data["outline"]["notes"] == ["正常长度的一条说明"]
        assert WIRE_TRIM_MARK not in p.skill_wire_json


class TestSixNodesAllLand:
    """六个项目级节点（init/setup/outline/outline_continue/review/synopsis）各跑一次，
    项目级字典键齐全、每个值与 blank_record 8 键同构。

    （deconstruct 不在其列：/deconstruct 端点发生在立项之前、没有项目可挂——
    generate 走章级 D2。这是任务书 §3.6「九个键」与代码现状的已知出入，见交付报告。）
    """

    EXPECTED_NODES = ["init", "setup", "outline", "outline_continue", "review", "synopsis"]

    @pytest.mark.asyncio
    async def test_all_six_nodes_land(self, sl, monkeypatch):
        install_llm(monkeypatch)
        user, p = await make_project(sl)

        # ① init（draft）
        async with sl() as db:
            await factory_mod.init_project(p.id, None, user, db)

        # ② setup（需要 novel + status=setup）
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            nv = Novel(user_id=user.id, title="[AI] 剑起青萍")
            db.add(nv)
            await db.flush()
            row.novel_id = nv.id
            row.status = "setup"
            await db.commit()
            novel_id = nv.id
        async with sl() as db:
            await factory_mod.setup_project(p.id, user, db)

        # ③ outline（setup 状态合法，跑完转 writing）
        async with sl() as db:
            await factory_mod.outline_project(p.id, user, db)

        # ④ outline_continue（writing + 显式目标章数）
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            row.target_chapters = 12
            await db.commit()
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            await factory_mod._outline_continue_core(row, user, db)

        # ⑤ review（给 outline 生成的第一章塞正文）
        async with sl() as db:
            chapter = (
                (await db.execute(select(Chapter).where(Chapter.novel_id == novel_id))).scalars().first()
            )
            chapter.content = "<p>" + "少年握紧残剑，山风把火把吹得明灭不定。" * 10 + "</p>"
            job = (
                (await db.execute(select(AiChapterJob).where(AiChapterJob.project_id == p.id)))
                .scalars()
                .first()
            )
            await db.commit()
            job_id = job.id
        async with sl() as db:
            await factory_mod.review_chapter(p.id, job_id, user, db)

        # ⑥ synopsis
        async with sl() as db:
            await extras_mod.gen_synopsis(p.id, user, db)

        async with sl() as db:
            row = await db.get(AiProject, p.id)
            raw = row.skill_wire_json
        assert raw, "六个节点跑完，skill_wire_json 仍是空的"
        data = json.loads(raw)
        expected = set(self.EXPECTED_NODES)
        assert set(data.keys()) == expected, f"键集不符：多了 {set(data) - expected}，少了 {expected - set(data)}"
        shape = set(skillwire.blank_record("x").keys())
        for node, record in data.items():
            assert isinstance(record, dict), f"{node} 的值必须是对象（D1），不是 {type(record)}"
            assert set(record.keys()) == shape, f"{node} 的 record 形状与 blank_record 不同构：{set(record.keys())}"
            assert record["node"] == node, f"{node} 键下的 record 节点名不符"
            assert isinstance(record["cards"], list) and record["chars"] >= 0

    @pytest.mark.asyncio
    async def test_keyed_merge_survives_separate_calls(self, sl, monkeypatch):
        """分开多次调用（模拟真实流水线时序）也归并在同一份 JSON 里，不是互相覆盖整列。"""
        install_llm(monkeypatch)
        user, p = await make_project(sl)
        async with sl() as db:
            await factory_mod.init_project(p.id, None, user, db)
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            row.status = "setup"
            await db.commit()
        async with sl() as db:
            await extras_mod.gen_synopsis(p.id, user, db)
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            data = json.loads(row.skill_wire_json)
        assert set(data.keys()) == {"init", "synopsis"}, f"两次调用的观测必须并存：{set(data.keys())}"


# ================================================================ 章级三条路径（D2/D3）


class TestChapterWireThreePaths:
    """UI / 夜跑 / 批量三条正文路径各自把 generate 节点的 records 落进该章 job。

    只覆盖 UI 一条 = D3 点名的「三条路径漂移在观测层重演」，这里三条各有一条证据。
    """

    def _assert_wire(self, job, expect_len: int | None = None):
        raw = getattr(job, "skill_wire_json", "")
        assert raw, "该路径没落任何章级观测"
        wire = json.loads(raw)
        assert isinstance(wire, list), f"章级必须是列表（D2 原样存）：{type(wire)}"
        assert wire, "章级观测列表为空"
        assert all(r["node"] == "generate" for r in wire), [r.get("node") for r in wire]
        shape = set(skillwire.blank_record("x").keys())
        for r in wire:
            assert set(r.keys()) == shape, f"章级 record 形状不同构：{set(r.keys())}"
        if expect_len is not None:
            assert len(wire) >= expect_len, f"至少 {expect_len} 条 record（方法论卡+文风实测），实得 {len(wire)}"
        return wire

    def test_ui_generate_path_lands_wire(self, monkeypatch):
        """UI 逐章：generate_chapter 全链路（真装配、真上游桩）→ job.skill_wire_json。"""
        p = _fake_project()
        job = _fake_chapter_job()
        chapter = _fake_chapter(content="<p>旧文</p>", word_count=5)
        novel = _fake_novel()
        inner = _assemble_db(chapter)
        inner.rows = {AiProject: p, Chapter: chapter, AiChapterJob: job, Novel: novel}
        outer = SeqDB(rows={AiProject: p, Chapter: chapter, AiChapterJob: job, Novel: novel})

        async def fake_pick(user, session, route_field):
            return make_ai_config()

        monkeypatch.setattr(factory_mod, "_pick_config", fake_pick)
        monkeypatch.setattr(factory_mod, "SessionLocal", _session_factory(inner))
        _patch_upstream(monkeypatch, "桥上的风很大，他握紧了刀。" * 20)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), outer)
            return [chunk async for chunk in resp.body_iterator]

        chunks = asyncio.run(run())
        assert chunks, "SSE 流一个字都没吐——主流程先坏了"
        self._assert_wire(job, expect_len=2)

    def test_nightly_path_lands_wire(self, monkeypatch):
        """夜跑：nightly._generate_one 全链路 → job.skill_wire_json（D3 的夜跑证据）。"""
        p = _fake_project()
        job = _fake_chapter_job()
        chapter = _fake_chapter()
        novel = _fake_novel()
        db = _assemble_db(chapter)
        db.rows = {AiProject: p, Chapter: chapter, AiChapterJob: job, Novel: novel}

        async def fake_pick(user, session, route_field):
            return make_ai_config()

        async def fake_chat(config, system, prompt, **kw):
            return "少年握紧残剑走进山门。" * 40

        async def fake_state(*a, **k):
            return True

        async def fake_rel(*a, **k):
            return None

        async def fake_split(*a, **k):
            return 1

        monkeypatch.setattr(nightly_mod, "_pick_config", fake_pick)
        monkeypatch.setattr(nightly_mod, "_chat_text", fake_chat)
        monkeypatch.setattr(nightly_mod, "_update_state_files", fake_state)
        monkeypatch.setattr(nightly_mod, "_sync_relations_from_chapter", fake_rel)
        monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", fake_split)
        monkeypatch.setattr(nightly_mod, "detect", lambda t: {"score": 95, "issues": []})
        monkeypatch.setattr(nightly_mod, "_record_usage", lambda *a, **k: None)

        result = asyncio.run(nightly_mod._generate_one(p, novel, job, chapter, 1, db))
        assert result.get("ok") is True, f"夜跑主流程先坏了：{result}"
        self._assert_wire(job, expect_len=2)

    def test_batch_path_lands_wire(self, monkeypatch):
        """批量：batch_run SSE 全链路 → job.skill_wire_json（D3 的批量证据）。"""
        p = _fake_project()
        job = _fake_chapter_job()
        chapter = _fake_chapter()
        novel = _fake_novel()
        db = SeqDB(
            rows={AiProject: p, Chapter: chapter, AiChapterJob: job, Novel: novel},
            # jobs / chapters / volumes + 装配 6 连查
            results=[[job], [chapter], [], [], [chapter], [], [], [], []],
        )

        async def fake_get_project(project_id, user, session):
            return p

        async def fake_pick(user, session, route_field):
            return make_ai_config()

        async def fake_chat(config, system, prompt, **kw):
            return "本章摘要：少年入局。"

        async def fake_state(*a, **k):
            return True

        async def fake_rel(*a, **k):
            return None

        async def fake_split(*a, **k):
            return 1

        monkeypatch.setattr(batch_mod, "_get_project", fake_get_project)
        monkeypatch.setattr(batch_mod, "_pick_config", fake_pick)
        monkeypatch.setattr(batch_mod, "_update_state_files", fake_state)
        monkeypatch.setattr(batch_mod, "_chat_text", fake_chat)
        monkeypatch.setattr(batch_mod, "_record_usage", lambda *a, **k: None)
        monkeypatch.setattr(batch_mod, "detect", lambda t: {"score": 95, "issues": []})
        monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", fake_split)
        monkeypatch.setattr(factory_mod, "_sync_relations_from_chapter", fake_rel)
        _patch_upstream(monkeypatch, "山道上落满黄叶，他一步步走上石阶。" * 20)

        async def run():
            resp = await batch_mod.batch_run(1, 1, fake_user(), db)
            return [chunk async for chunk in resp.body_iterator]

        chunks = asyncio.run(run())
        assert any("chapter_done" in c for c in chunks), f"批量流没跑到 chapter_done：{chunks[-3:]}"
        self._assert_wire(job, expect_len=2)

    def test_job_wire_appends_across_reruns(self):
        """同章重跑：列表追加（键序即注入次序），不覆盖上一轮的记录。"""
        job = SimpleNamespace(skill_wire_json="")
        factory_mod._save_job_wire(job, [skillwire.blank_record("generate")])
        factory_mod._save_job_wire(job, [skillwire.blank_record("generate")])
        wire = json.loads(job.skill_wire_json)
        assert len(wire) == 2, f"重跑必须追加而非覆盖：{len(wire)}"

    def test_job_wire_corrupt_old_value_rebuilds_with_trace(self):
        job = SimpleNamespace(skill_wire_json="not-json-at-all{{")
        rec = skillwire.blank_record("generate")
        factory_mod._save_job_wire(job, [rec])
        wire = json.loads(job.skill_wire_json)
        assert wire and wire[0]["node"] == "generate"
        assert any("解析失败" in n or "重建" in n for n in wire[0]["notes"]), wire[0]["notes"]

    def test_job_wire_empty_records_never_clobber(self):
        """records 为空（如 include_skill=False 的诊断路径）不许洗掉已有观测。"""
        keep = '[{"node": "generate"}]'
        job = SimpleNamespace(skill_wire_json=keep)
        factory_mod._save_job_wire(job, [])
        assert job.skill_wire_json == keep


# ================================================================ 观测写入绝不抛


def _poison_dumps(monkeypatch):
    """让 json.dumps 只对含 wire record 的载荷（record 本体 / 章级列表 / 项目级字典）爆炸，
    其余照常——否则立项端点自己的 book_spec_json 也写不进去，测试就红错了地方。"""
    real = json.dumps

    def _is_record(obj):
        return isinstance(obj, dict) and "node" in obj and "cards" in obj

    def poisoned(obj, *a, **kw):
        hit = _is_record(obj)
        if not hit and isinstance(obj, list):
            hit = any(_is_record(x) for x in obj)
        if not hit and isinstance(obj, dict):
            hit = any(_is_record(v) for v in obj.values())
        if hit:
            raise RuntimeError("观测写入炸了")
        return real(obj, *a, **kw)

    monkeypatch.setattr(json, "dumps", poisoned)


class TestWireNeverThrows:
    @pytest.mark.asyncio
    async def test_poisoned_dumps_keeps_init_endpoint_alive(self, sl, monkeypatch):
        """项目级：观测写入炸掉 → 立项端点照常 200、主数据照常落库。"""
        install_llm(monkeypatch)
        _poison_dumps(monkeypatch)
        user, p = await make_project(sl)
        async with sl() as db:
            out = await factory_mod.init_project(p.id, None, user, db)
        assert out and out.get("id") == p.id, "立项端点没返回正常结果"
        async with sl() as db:
            row = await db.get(AiProject, p.id)
        assert row.book_spec_json, "主数据（book_spec_json）必须照常落库"
        assert row.skill_wire_json == "", "观测写失败就该空着，不许留半个脏值"

    def test_poisoned_dumps_keeps_nightly_alive(self, monkeypatch):
        """章级：观测写入炸掉 → 夜跑照常出稿（技能是增强不是依赖的观测版）。"""
        p = _fake_project()
        job = _fake_chapter_job()
        chapter = _fake_chapter()
        novel = _fake_novel()
        db = _assemble_db(chapter)
        db.rows = {AiProject: p, Chapter: chapter, AiChapterJob: job, Novel: novel}

        async def fake_pick(user, session, route_field):
            return make_ai_config()

        async def fake_chat(config, system, prompt, **kw):
            return "少年握紧残剑走进山门。" * 40

        async def fake_state(*a, **k):
            return True

        async def fake_rel(*a, **k):
            return None

        async def fake_split(*a, **k):
            return 1

        monkeypatch.setattr(nightly_mod, "_pick_config", fake_pick)
        monkeypatch.setattr(nightly_mod, "_chat_text", fake_chat)
        monkeypatch.setattr(nightly_mod, "_update_state_files", fake_state)
        monkeypatch.setattr(nightly_mod, "_sync_relations_from_chapter", fake_rel)
        monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", fake_split)
        monkeypatch.setattr(nightly_mod, "detect", lambda t: {"score": 95, "issues": []})
        monkeypatch.setattr(nightly_mod, "_record_usage", lambda *a, **k: None)
        _poison_dumps(monkeypatch)

        result = asyncio.run(nightly_mod._generate_one(p, novel, job, chapter, 1, db))
        assert result.get("ok") is True, f"观测炸了主流程也必须活着：{result}"
        assert chapter.content, "正文必须照常产出"
        assert job.skill_wire_json == "", "观测写失败就该空着，不许留半个脏值"


# ================================================================ 硬闸：轮数滚动 + 载荷必≤上限
#
# D-4 的「同章重跑追加」若无轮数上限，records 线性无界增长（实测 ~330 字/轮，
# 40 轮 ≈ 13.6K、160 轮 ≈ 54.6K），端点还会把所有章一次性吐出——观测层自己
# 变成故障源。这组测试钉三条铁律：
#   1. 轮数硬闸：章级只保留最近 _WIRE_MAX_ROUNDS 轮，无条件生效（不是超限才触发）；
#   2. 丢弃必须可见：滚动丢弃在最新一条 notes 留痕，绝不静默；
#   3. 载荷必≤上限：notes 削光仍超限 → 截列表本身并留痕，绝不返回超限载荷。


def _marker_record(i: int):
    """带可区分标记的 record（~330 字，贴近真实 generate record 的体积口径）。"""
    rec = skillwire.blank_record("generate")
    rec["notes"] = [f"ROUND-MARK-{i}", "填充" * 50]
    rec["cards"] = [
        {
            "slug": "chapter-hook-crafter",
            "chars": 100,
            "tool_chars": 0,
            "manual_chars": 0,
            "loaded": [{"rel": "references/hook-typology.md", "title": "钩子类型", "chars": 50, "truncated": False}],
            "notes": [],
        }
    ]
    return rec


class TestRollingWindowCap:
    def test_40_rounds_capped_and_recent_kept(self):
        """40 轮重跑：落库 ≤ 上限、只留最近 _WIRE_MAX_ROUNDS 轮、留的必须是**最新的**。"""
        job = SimpleNamespace(skill_wire_json="")
        for i in range(40):
            factory_mod._save_job_wire(job, [_marker_record(i)])
        raw = job.skill_wire_json
        assert len(raw) <= WIRE_LIMIT, f"40 轮后仍超限：{len(raw)} 字——轮数硬闸没生效"
        wire = json.loads(raw)
        assert len(wire) == factory_mod._WIRE_MAX_ROUNDS, (
            f"保留轮数 {len(wire)} != _WIRE_MAX_ROUNDS({factory_mod._WIRE_MAX_ROUNDS})"
        )
        start = 40 - factory_mod._WIRE_MAX_ROUNDS
        marks = [n for r in wire for n in r["notes"] if str(n).startswith("ROUND-MARK-")]
        assert marks == [f"ROUND-MARK-{i}" for i in range(start, 40)], f"保留的不是最近几轮：{marks}"

    def test_160_rounds_capped_and_recent_kept(self):
        """160 轮重跑：同样封顶——闸是常数级的，不随轮数漂移。"""
        job = SimpleNamespace(skill_wire_json="")
        for i in range(160):
            factory_mod._save_job_wire(job, [_marker_record(i)])
        raw = job.skill_wire_json
        assert len(raw) <= WIRE_LIMIT, f"160 轮后仍超限：{len(raw)} 字"
        wire = json.loads(raw)
        assert len(wire) == factory_mod._WIRE_MAX_ROUNDS
        start = 160 - factory_mod._WIRE_MAX_ROUNDS
        marks = [n for r in wire for n in r["notes"] if str(n).startswith("ROUND-MARK-")]
        assert marks == [f"ROUND-MARK-{i}" for i in range(start, 160)]

    def test_roll_drop_leaves_visible_trace(self):
        """滚动丢弃必须可见：最新一条 notes 里有留痕，不许静默。

        留痕写的是**本次落库丢弃的轮数**：一次性追加 40 条（脏数据重建后批量落库
        的形状）→ 丢 32 轮、留痕写明 32；逐轮调用则是窗口滑动、每次丢 1 轮。
        """
        job = SimpleNamespace(skill_wire_json="")
        factory_mod._save_job_wire(job, [_marker_record(i) for i in range(40)])
        wire = json.loads(job.skill_wire_json)
        assert len(wire) == factory_mod._WIRE_MAX_ROUNDS
        traces = [n for r in wire for n in r["notes"] if "已滚动丢弃" in str(n)]
        assert traces, "滚动丢弃没留任何痕迹——静默丢弃被逮住了"
        assert "32" in str(traces[0]), f"留痕应写明本次丢了几轮（32 轮）：{traces[0]}"

    def test_sliding_window_drop_also_leaves_trace(self):
        """逐轮重跑（正常使用形状）：窗口每轮滑动一格，同样必须留痕。"""
        job = SimpleNamespace(skill_wire_json="")
        for i in range(40):
            factory_mod._save_job_wire(job, [_marker_record(i)])
        wire = json.loads(job.skill_wire_json)
        assert len(wire) == factory_mod._WIRE_MAX_ROUNDS
        assert any("已滚动丢弃" in str(n) for r in wire for n in r["notes"]), "滑动窗口丢弃没留痕"

    def test_within_round_cap_leaves_no_trace(self):
        """没超轮数不许有留痕（防误报——留痕只属于真实的丢弃）。

        用固定小轮数构造（不引用 _WIRE_MAX_ROUNDS 做循环量），这样「上限被改大」
        的变异下本测试快速绿，由 40/160 轮测试红——变异自证才能跑得动。
        """
        job = SimpleNamespace(skill_wire_json="")
        for i in range(3):
            factory_mod._save_job_wire(job, [_marker_record(i)])
        wire = json.loads(job.skill_wire_json)
        assert len(wire) == 3
        assert len(wire) <= factory_mod._WIRE_MAX_ROUNDS
        assert all("已滚动丢弃" not in str(n) for r in wire for n in r["notes"])

    def test_hard_cap_truncates_list_itself_with_trace(self):
        """兜底闸：notes 救不了的主体膨胀（8 条 × ~2K 卡元数据）→ 截**列表本身**并留痕。

        钉的是「绝不返回超限载荷」：单条 record 的不可裁主体（cards 元数据）远小于
        上限（见 _WIRE_SIZE_LIMIT 注释的量法），所以截到 1 条必然装得下。
        """

        def heavy(i):
            rec = skillwire.blank_record("generate")
            rec["notes"] = [f"ROUND-MARK-{i}"]  # notes 很小，削不动
            rec["cards"] = [
                {
                    "slug": f"card-{i}",
                    "chars": 10,
                    "tool_chars": 0,
                    "manual_chars": 0,
                    "loaded": [
                        {"rel": f"references/doc-{j}.md", "title": "标题" * 20, "chars": 9, "truncated": False}
                        for j in range(30)
                    ],
                    "notes": [],
                }
            ]
            return rec

        job = SimpleNamespace(skill_wire_json="")
        for i in range(8):
            factory_mod._save_job_wire(job, [heavy(i)])
        raw = job.skill_wire_json
        assert len(raw) <= WIRE_LIMIT, f"主体膨胀场景仍超限：{len(raw)} 字——兜底截断没生效"
        wire = json.loads(raw)
        assert len(wire) < 8, "notes 已无可削，超限只能靠截列表本身——列表没被截"
        assert len(wire) >= 1
        traces = [n for r in wire for n in r["notes"] if "已丢弃" in str(n)]
        assert traces, "兜底截断没留痕——静默丢弃被逮住了"

    def test_project_level_measured_ceiling_is_docked(self):
        """D1 项目级判据（碧霄要求量出来，不许「大概不会超吧」）：

        六节点真实 inject 各跑一次落库，实测总体积必须远小于上限。
        量法：每节点同键**覆盖**不叠加（D1 语义），六键总量 = 6 × 单条 record，
        单条主体 = 卡名计数 + loaded 元数据（受注入预算约束，实测每卡 ≤ 4 条 × ~150 字）。
        实测口径 ~2-3K，这里钉 < 6000 留出病态余量；哪天膨胀破线，此测试红。
        """
        p = SimpleNamespace(skill_wire_json="")
        total_prompt = "少年携残剑入局的修行故事"
        for node in ("init", "setup", "outline", "outline_continue", "review", "synopsis"):
            _t, record = factory_mod._wire_skill(node, total_prompt)
            factory_mod._save_project_wire(p, node, record)
        size = len(p.skill_wire_json)
        assert size <= WIRE_LIMIT, f"项目级六节点实测 {size} 字已超上限"
        assert size < 6000, f"项目级实测 {size} 字，超出注释里的实测口径（~3K）——主体在膨胀，查 loaded 数量"


def _skill_block_calls_outside_assembly(src_root: Path) -> list[str]:
    """枚举 src_root 源码树里「_assemble_context 函数体之外」的 _chapter_skill_block 调用点。

    清单从文件系统取（app/*.py + app/routers/*.py），**不手写文件名**——
    手写名单必然落后于现实，新建路由文件就是逃逸口（N1，碧霄裁决）。
    返回违规清单（空 = 通过）；ai_factory.py 本身不在此扫（由
    test_chapter_skill_block_only_called_from_assembly 单独守「只许装配层一处」）。
    """
    offenders: list[str] = []
    files = sorted(src_root.glob("*.py")) + sorted((src_root / "routers").glob("*.py"))
    for f in files:
        if f.name == "ai_factory.py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                    if name == "_chapter_skill_block":
                        offenders.append(f"{f.name}:{fn.name}")
    return offenders


class TestAssemblyIsTheOnlySkillBlockCaller:
    """D-1 残留风险的机械闸：绕开装配层直接调 _chapter_skill_block = 观测漏接。

    漂移要用 AST 判据拦，不能靠注释里的自觉。
    扫描清单从文件系统枚举（app/*.py + app/routers/*.py），不手写文件名——
    手写名单必然落后于现实，新建路由文件就是逃逸口（N1，碧霄裁决）。
    """

    def test_chapter_skill_block_only_called_from_assembly(self):
        src = (APP / "routers" / "ai_factory.py").read_text(encoding="utf-8")
        found = 0
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if isinstance(call, ast.Call):
                    name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                    if name == "_chapter_skill_block":
                        found += 1
                        assert fn.name == "_assemble_context", (
                            f"{fn.name} 里出现了 _chapter_skill_block 调用——绕开了章级观测落库（漏接）"
                        )
        assert found >= 1, "_chapter_skill_block 没有任何调用点——装配链路被拆了？"

    def test_no_other_router_file_calls_it(self):
        offenders = _skill_block_calls_outside_assembly(APP)
        assert offenders == [], f"装配层之外出现了 _chapter_skill_block 调用：{offenders}"

    def test_enumerated_scan_actually_bites(self, tmp_path):
        """守卫自身的咬合自证：给枚举扫描喂一棵带违规调用的假源码树，必须报出清单。

        「检查没报错 ≠ 检查在干活」——这条钉住扫描本身真的在干活，
        新建文件里的违规调用不可能逃逸。
        """
        routers = tmp_path / "routers"
        routers.mkdir()
        (routers / "evil.py").write_text(
            "async def handler(p, n, c, db):\n"
            "    block, records = await _chapter_skill_block(p, n, c, db, 1)\n",
            encoding="utf-8",
        )
        offenders = _skill_block_calls_outside_assembly(tmp_path)
        assert offenders == ["evil.py:handler"], f"枚举扫描没咬住新文件里的违规调用：{offenders}"


class TestWhitelistBitesOnRenamedKeys:
    """M2（评审 major）的持久化形态：白名单识别不得依赖「某个调度键在场」。

    评审实测：旧判据（`"slug" in obj` 这类**单键在场**识别）下，把 skillwire 的
    `slug` 改名 `slugx`、doc 层调度键失配，39 项**全绿**——该层认不出自己该管谁，
    直接跳过 = 护栏在形状被破坏时静默失效，而不是报警。
    新判据是「冻结键集 ⊆ 实际键集」子集匹配 + 未知形状判红；这三条测试把评审的
    手动变异固化为永久内置（每轮都咬，不靠有人记得手动放变异）。
    """

    @staticmethod
    def _card():
        return {
            "slug": "chapter-hook-crafter",
            "chars": 100,
            "tool_chars": 0,
            "manual_chars": 0,
            "loaded": [],
            "notes": [],
        }

    @staticmethod
    def _renamed(obj, old, new):
        obj = dict(obj)
        obj[new] = obj.pop(old)
        return obj

    def _save_and_check(self, rec, layer: str):
        job = SimpleNamespace(skill_wire_json="")
        factory_mod._save_job_wire(job, [rec])
        with pytest.raises(AssertionError, match="认不出形状"):
            _assert_shape_closed(json.loads(job.skill_wire_json), path=f"mutated:{layer}")

    def test_record_dispatch_key_renamed_bites(self):
        """record 层调度键 `cards` 改名 → 键集失配 → 必须红（不许跳过）。"""
        rec = skillwire.blank_record("generate")
        rec = self._renamed(rec, "cards", "cardsx")
        self._save_and_check(rec, "record")

    def test_card_dispatch_key_renamed_bites(self):
        """card 层调度键 `slug` 改名 → 必须红（评审实测旧判据下全绿）。"""
        rec = skillwire.blank_record("generate")
        rec["cards"] = [self._renamed(self._card(), "slug", "slugx")]
        self._save_and_check(rec, "card")

    def test_doc_dispatch_key_renamed_bites(self):
        """doc 层调度键 `rel` 改名 → 必须红（评审实测旧判据下全绿）。"""
        rec = skillwire.blank_record("generate")
        card = self._card()
        card["loaded"] = [self._renamed({"rel": "references/x.md", "title": "t", "chars": 9, "truncated": False}, "rel", "relx")]
        rec["cards"] = [card]
        self._save_and_check(rec, "doc")


# ================================================================ 护栏：不存正文


#: 结构护栏的封闭键集（与 skillwire 冻结形状一一对应，实测采集于真实 inject 输出）：
#: record 8 键 / card 6 键 / doc 4 键。观测树里出现任何额外键（prompt_dump / text /
#: content……）都算私存正文，红。
ALLOWED_RECORD_KEYS = {"node", "chars", "tool_chars", "manual_chars", "total_chars", "truncated", "cards", "notes"}
ALLOWED_CARD_KEYS = {"slug", "chars", "tool_chars", "manual_chars", "loaded", "notes"}
ALLOWED_DOC_KEYS = {"rel", "title", "chars", "truncated"}


def _matches_known_shape(keys: set) -> bool:
    """子集匹配：三种冻结键集任一是实际键集的子集即认得。三层键集互相不包含，无歧义。"""
    return ALLOWED_RECORD_KEYS <= keys or ALLOWED_CARD_KEYS <= keys or ALLOWED_DOC_KEYS <= keys


def _assert_shape_closed(obj, path: str = "root") -> None:
    """递归校验观测树只出现封闭键集里的形状（M2 收紧版，评审实测旧判据静默失效）。

    识别用「冻结键集 ⊆ 实际键集」的**子集匹配**，不是「某个调度键在场」：
    调度键改名（slug→slugx / rel→relx / cards→cardsx）时子集自然失配，
    该 dict 掉进 else 分支被判红——而不是像旧判据那样认不出自己、静默跳过。
    匹配不上任何已知形状的 dict 一律红：未知形状恰恰是最该报警的情况，
    跳过等于给新字段开后门。
    """
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _assert_shape_closed(item, f"{path}[{i}]")
        return
    if not isinstance(obj, dict):
        return
    keys = set(obj)
    if ALLOWED_RECORD_KEYS <= keys:
        allowed = ALLOWED_RECORD_KEYS
    elif ALLOWED_CARD_KEYS <= keys:
        allowed = ALLOWED_CARD_KEYS
    elif ALLOWED_DOC_KEYS <= keys:
        allowed = ALLOWED_DOC_KEYS
    else:
        # 不是三种冻结形状：唯一合法形态是 D1 外层归并字典——键=节点名，
        # 每个值都必须是「能匹配已知冻结形状的 dict」。出现标量 / 列表值 /
        # 认不出形状的子 dict，一律判红（不许静默放过）。
        # ⚠️ 值匹配形状后**必须继续递归深入**——只看形状标签就 return 是
        # 静默失效的另一种形状（首轮变异自证抓到：card 层改名在项目级被放过）。
        for k, v in obj.items():
            if not (isinstance(v, dict) and _matches_known_shape(set(v))):
                raise AssertionError(
                    f"{path}.{k} 是观测树里认不出形状的值（type={type(v).__name__}）"
                    f"——未知形状一律红，不给新字段开后门"
                )
            _assert_shape_closed(v, f"{path}.{k}")
        return
    extra = keys - allowed
    assert not extra, f"{path} 出现冻结形状之外的键：{sorted(extra)}（keys={sorted(keys)}）"
    for k, v in obj.items():
        _assert_shape_closed(v, f"{path}.{k}")


class TestGuardrailNoPromptBody:
    def test_project_wire_never_carries_prompt_body(self):
        """项目级：注入时带着完整 prompt（含哨兵），落库 JSON 里绝不能出现它。"""
        prompt = f"【原始 prompt 正文】{SENTINEL}\n请为这个创意做小说立项，输出 JSON。"
        _t, record = factory_mod._wire_skill("outline", prompt)
        p = SimpleNamespace(skill_wire_json="")
        factory_mod._save_project_wire(p, "outline", record)
        assert SENTINEL not in p.skill_wire_json, "prompt 正文漏进了项目观测表！"
        data = json.loads(p.skill_wire_json)
        assert data["outline"]["node"] == "outline"

    def test_chapter_wire_never_carries_chapter_body(self, monkeypatch):
        """章级：实测语料（含哨兵的章节正文）只变成 tool_chars 计数，不许进观测 JSON。"""
        corpus = [("第1章", f"他握剑。{SENTINEL}雨落了一夜。")]

        async def fake_corpus(db, nid, limit=40):
            return corpus

        monkeypatch.setattr(skills_mod, "novel_texts", fake_corpus)
        p = _fake_project(book_spec_json="", style_notes="", plot_arcs="", character_state="", global_summary="")
        novel = _fake_novel()
        chapter = _fake_chapter()
        job = _fake_chapter_job()
        db = SeqDB(results=[[], [chapter], [], [], []])
        asyncio.run(factory_mod._assemble_context(p, novel, chapter, job, db))
        assert job.skill_wire_json, "装配没落任何观测"
        assert SENTINEL not in job.skill_wire_json, "章节正文漏进了章级观测表！"

    def test_stored_wire_shape_is_closed(self):
        """结构护栏：观测 JSON 树只允许冻结键集（record 8 键 / card 6 键 / doc 4 键）。

        字符串哨兵只能抓「注入的内容漏进表」；这条抓「落库代码私加字段」——
        不管加的是 prompt 正文还是任何别的东西，多一个键就红。
        """
        for node in ("init", "setup", "outline", "outline_continue", "generate", "review", "synopsis"):
            _t, record = factory_mod._wire_skill(node, "任务文本" + SENTINEL)
            p = SimpleNamespace(skill_wire_json="")
            factory_mod._save_project_wire(p, node, record)
            _assert_shape_closed(json.loads(p.skill_wire_json), path=f"project:{node}")
            assert SENTINEL not in p.skill_wire_json

    def test_stored_job_wire_shape_is_closed(self, monkeypatch):
        """章级观测树同样封闭（含带真实实测数据的 style-fingerprint record）。"""
        corpus = [("第1章", "他握剑。雨落了一夜，灯一盏盏灭掉。" * 120)]

        async def fake_corpus(db, nid, limit=40):
            return corpus

        monkeypatch.setattr(skills_mod, "novel_texts", fake_corpus)
        p = _fake_project(book_spec_json="", style_notes="", plot_arcs="", character_state="", global_summary="")
        novel = _fake_novel()
        chapter = _fake_chapter()
        job = _fake_chapter_job()
        db = SeqDB(results=[[], [chapter], [], [], []])
        asyncio.run(factory_mod._assemble_context(p, novel, chapter, job, db))
        _assert_shape_closed(json.loads(job.skill_wire_json), path="job")


# ================================================================ 查询端点


class TestSkillWireEndpoint:
    @pytest.mark.asyncio
    async def test_returns_nodes_and_sorted_chapters(self, sl):
        """正常返回：nodes + 章级按章号升序、只列有观测的章（没跑过的章不给空壳）。"""
        user, p = await make_project(sl)
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            nv = Novel(user_id=user.id, title="书")
            db.add(nv)
            await db.flush()
            row.novel_id = nv.id
            row.skill_wire_json = json.dumps({"outline": skillwire.blank_record("outline")}, ensure_ascii=False)
            made = {}
            for so in (2, 0, 1):
                c = Chapter(novel_id=nv.id, title=f"章-排序{so}", content="", sort_order=so, status="draft")
                db.add(c)
                await db.flush()
                made[so] = c
            # 排序 2 与 0 的章有观测；排序 1 的章没跑过 → 不该出现
            for so in (2, 0):
                j = AiChapterJob(project_id=p.id, chapter_id=made[so].id, status="done")
                j.skill_wire_json = json.dumps([skillwire.blank_record("generate")], ensure_ascii=False)
                db.add(j)
            db.add(AiChapterJob(project_id=p.id, chapter_id=made[1].id, status="pending"))
            await db.commit()

        async with sl() as db:
            out = await factory_mod.get_skill_wire(p.id, 50, None, user, db)

        assert out["project_id"] == p.id
        assert set(out["nodes"].keys()) == {"outline"}
        assert out["total_chapters"] == 2 and out["truncated"] is False
        chapters = out["chapters"]
        assert len(chapters) == 2, f"没跑过的章不许出现空壳：{len(chapters)}"
        nums = [c["number"] for c in chapters]
        assert nums == sorted(nums), f"章级必须按章号升序：{nums}"
        for c in chapters:
            assert set(c.keys()) == {"chapter_id", "number", "title", "wire"}
            assert c["wire"] and c["wire"][0]["node"] == "generate"

    @pytest.mark.asyncio
    async def test_empty_project_returns_empty_shapes(self, sl):
        user, p = await make_project(sl)
        async with sl() as db:
            out = await factory_mod.get_skill_wire(p.id, 50, None, user, db)
        assert out == {"project_id": p.id, "nodes": {}, "chapters": [], "total_chapters": 0, "truncated": False}

    @pytest.mark.asyncio
    async def test_missing_project_404(self, sl):
        user, _p = await make_project(sl)
        async with sl() as db:
            with pytest.raises(HTTPException) as ei:
                await factory_mod.get_skill_wire(9999, 50, None, user, db)
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_other_users_project_404(self, sl):
        _owner, p = await make_project(sl)
        async with sl() as db:
            stranger = User(username="别人", password_hash="x")
            db.add(stranger)
            await db.commit()
            stranger_id = stranger.id
        async with sl() as db:
            stranger = await db.get(User, stranger_id)
            with pytest.raises(HTTPException) as ei:
                await factory_mod.get_skill_wire(p.id, 50, None, stranger, db)
        assert ei.value.status_code == 404

    @pytest.mark.asyncio
    async def test_corrupt_data_degrades_not_500(self, sl):
        """项目级与章级数据各自烂掉 → 对应字段降级 _raw_error，整体照常 200。"""
        user, p = await make_project(sl)
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            nv = Novel(user_id=user.id, title="书")
            db.add(nv)
            await db.flush()
            row.novel_id = nv.id
            row.skill_wire_json = "{项目级烂数据"
            c = Chapter(novel_id=nv.id, title="章", content="", sort_order=0, status="draft")
            db.add(c)
            await db.flush()
            j = AiChapterJob(project_id=p.id, chapter_id=c.id, status="done")
            j.skill_wire_json = "章级烂数据{{"
            db.add(j)
            await db.commit()

        async with sl() as db:
            out = await factory_mod.get_skill_wire(p.id, 50, None, user, db)

        assert "_raw_error" in out["nodes"], "项目级脏数据必须降级而不是 500"
        assert out["chapters"], "章级行还在，只是数据烂了"
        assert out["chapters"][0]["wire"] and "_raw_error" in out["chapters"][0]["wire"][0]


class TestEndpointPaging:
    """M3（碧霄规格补漏）：端点响应按章数无界增长（评审实测 1000 章 = 3.76MB，无上界）。

    四件套：``?limit=``（默认 50，钳 200——形状照抄 ai.py /history 的 min(limit, 200)）、
    ``?chapter_id=`` 精确查（写哪章查哪章，最常用姿势）、``total_chapters``、``truncated``。
    **截断必须可见**：查观测的人若不知道"还有 250 章没给我"，就会基于残缺数据下结论，
    比体积大更危险。

    取向裁决（碧霄，与评审建议相反、按她的来）：chapters 保持**章号升序**，limit 取
    **尾部窗口**（最近 N 章）——与 _WIRE_MAX_ROUNDS 保留最近几轮同一取向；升序保证
    人眼读起来仍是"第1章→第N章"的自然顺序，只是起点在尾部；默认给头部等于默认展示
    最不可能有人关心的部分（写作进度在书尾）。
    """

    async def _make_wired_chapters(self, sl, n: int):
        """造 n 章各带一份章级观测（wire 非空），返回 (user_id, project_id, {序号: chapter_id})。"""
        user, p = await make_project(sl)
        async with sl() as db:
            row = await db.get(AiProject, p.id)
            nv = Novel(user_id=user.id, title="书")
            db.add(nv)
            await db.flush()
            row.novel_id = nv.id
            ids = {}
            for i in range(n):
                c = Chapter(novel_id=nv.id, title=f"第{i + 1}章", content="", sort_order=i, status="draft")
                db.add(c)
                await db.flush()
                ids[i] = c.id
                j = AiChapterJob(project_id=p.id, chapter_id=c.id, status="done")
                j.skill_wire_json = json.dumps([skillwire.blank_record("generate")], ensure_ascii=False)
                db.add(j)
            await db.commit()
        return user.id, p.id, ids

    @pytest.mark.asyncio
    async def test_limit_takes_tail_window_ascending(self, sl):
        """limit 生效时保留**尾部**（章号最大的那些），且窗口内仍升序——不是头部。"""
        uid, pid, _ids = await self._make_wired_chapters(sl, 5)
        async with sl() as db:
            user = await db.get(User, uid)
            out = await factory_mod.get_skill_wire(pid, 2, None, user, db)
        nums = [c["number"] for c in out["chapters"]]
        assert nums == [4, 5], f"limit 必须取尾部窗口（章号最大的），实得 {nums}"
        assert nums == sorted(nums), "窗口内仍要升序"

    @pytest.mark.asyncio
    async def test_truncated_and_total_consistency(self, sl):
        """截断可见性：truncated 与 total_chapters 数值自洽，返回条数 == min(total, 钳后 limit)。"""
        uid, pid, _ids = await self._make_wired_chapters(sl, 5)
        async with sl() as db:
            user = await db.get(User, uid)
            small = await factory_mod.get_skill_wire(pid, 2, None, user, db)
            assert small["total_chapters"] == 5 and small["truncated"] is True
            assert len(small["chapters"]) == min(5, 2) == 2
            exact = await factory_mod.get_skill_wire(pid, 5, None, user, db)
            assert exact["truncated"] is False and len(exact["chapters"]) == 5
            big = await factory_mod.get_skill_wire(pid, 500, None, user, db)
            assert big["truncated"] is False and len(big["chapters"]) == 5

    @pytest.mark.asyncio
    async def test_limit_clamped_to_200(self, sl):
        """钳上限 200（照抄 /history 形状）：limit=10**6 也只回 200，truncated 如实，窗口仍取尾部。"""
        uid, pid, _ids = await self._make_wired_chapters(sl, 201)
        async with sl() as db:
            user = await db.get(User, uid)
            out = await factory_mod.get_skill_wire(pid, 10**6, None, user, db)
        assert out["total_chapters"] == 201
        assert len(out["chapters"]) == 200, f"limit 未钳到 200：{len(out['chapters'])}"
        assert out["truncated"] is True
        nums = [c["number"] for c in out["chapters"]]
        assert nums == sorted(nums)
        assert nums[0] == 2 and nums[-1] == 201, f"钳制后的窗口同样取尾部：{nums[:3]}…{nums[-3:]}"

    @pytest.mark.asyncio
    async def test_limit_201_exact_over_by_one(self, sl):
        """limit=201 恰好越界一档：钳到 200、窗口取尾部（碧霄点名的确认档，与 10**6 同分支）。"""
        uid, pid, _ids = await self._make_wired_chapters(sl, 201)
        async with sl() as db:
            user = await db.get(User, uid)
            out = await factory_mod.get_skill_wire(pid, 201, None, user, db)
        assert len(out["chapters"]) == 200
        assert out["truncated"] is True
        nums = [c["number"] for c in out["chapters"]]
        assert nums[0] == 2 and nums[-1] == 201

    # ---- 下限边界（碧霄变异 C 实证：拆掉 max(1,·) 后 limit=0 全绿 = 零覆盖）----
    # `entries[-limit:]` 在 limit=0 时因 Python 切片 `-0 == 0` **原地反转成取全部**：
    # M3 立的"响应有界"被一个请求打穿，且打穿方式恰好是返回最多数据（最坏攻击面）。
    # 这组测试就是 max(1,·) 那行承重墙的测试——墙不许拆，拆墙者必须红。
    # 判据用 `== 1`（测试数据恒 5 章 > 钳后 limit 1，必返回恰 1 条最新章）：
    # 比 `<= 1` 上限更严，且让负数变异（切片取到 0 条或尾部之后）也咬得住。

    @pytest.mark.asyncio
    async def test_limit_zero_does_not_explode_to_full(self, sl):
        """limit=0 → 钳到 1：绝不许因 `-0==0` 反转成全量返回。"""
        uid, pid, _ids = await self._make_wired_chapters(sl, 5)
        async with sl() as db:
            user = await db.get(User, uid)
            out = await factory_mod.get_skill_wire(pid, 0, None, user, db)
        assert len(out["chapters"]) == 1, f"limit=0 必须恰 1 条（绝不许全量 5 条）：{len(out['chapters'])}"
        assert out["truncated"] is True
        assert out["total_chapters"] == 5, "total_chapters 是全量口径，不随 limit 缩水"

    @pytest.mark.asyncio
    async def test_limit_negative_does_not_explode_to_full(self, sl):
        """limit=-5 负数边界：同样钳到 1。剥掉 max(1,·) 时 `entries[5:]` 取到 0 条——
        切片方向第二次反转，这条让负数路径的拆墙者也红。"""
        uid, pid, _ids = await self._make_wired_chapters(sl, 5)
        async with sl() as db:
            user = await db.get(User, uid)
            out = await factory_mod.get_skill_wire(pid, -5, None, user, db)
        assert len(out["chapters"]) == 1, f"负 limit 必须恰 1 条：{len(out['chapters'])}"
        assert out["truncated"] is True

    @pytest.mark.asyncio
    async def test_lower_bound_guard_explicit(self, sl):
        """下限守卫（碧霄第 4 条）：显式钉边界值集合 {0, -1, -100} 的返回条数。

        不用 `!= 全量` 这类会随总数漂移的判据；边界值必须显式列进测试输入——
        `if x:` / `[-n:]` / `or default` 家族的边界值在语言层被静默反转，
        代码看着对、行为是反的（同 t1 `if requested:` 把 [] 当"未指定"）。
        """
        uid, pid, _ids = await self._make_wired_chapters(sl, 5)
        async with sl() as db:
            user = await db.get(User, uid)
            for bad in (0, -1, -100):
                out = await factory_mod.get_skill_wire(pid, bad, None, user, db)
                assert len(out["chapters"]) == 1, f"limit={bad} 应钳到 1 条，实得 {len(out['chapters'])}"
                assert out["truncated"] is True

    @pytest.mark.asyncio
    async def test_chapter_id_exact_lookup(self, sl):
        """chapter_id 精确查（写哪章查哪章）：单章返回、不计截断；无观测的章给空且如实。"""
        uid, pid, ids = await self._make_wired_chapters(sl, 3)
        async with sl() as db:
            user = await db.get(User, uid)
            one = await factory_mod.get_skill_wire(pid, 50, ids[1], user, db)
            assert [c["number"] for c in one["chapters"]] == [2]
            assert one["total_chapters"] == 1 and one["truncated"] is False
            none = await factory_mod.get_skill_wire(pid, 50, 99999, user, db)
            assert none["chapters"] == [] and none["total_chapters"] == 0 and none["truncated"] is False


# ================================================================ 接线在位（AST 守卫）


class TestCallSitesSaveWire:
    """落库调用被删时这里要报警——行为测试只证明「现在能落」，AST 守卫证明「没被拆」。"""

    SITES = {
        "ai_factory.py": ["init", "setup", "outline", "outline_continue", "review"],
        "ai_extras.py": ["synopsis"],
    }

    @staticmethod
    def _scan(src: str):
        """返回 {node: (wire_line, save_line)}——同函数内的注入行与落库行。"""
        found: dict[str, tuple[int, int]] = {}
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            wires: dict[str, int] = {}
            saves: dict[str, int] = {}
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                name = getattr(call.func, "id", None) or getattr(call.func, "attr", None)
                if name == "_wire_skill" and call.args:
                    first = call.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        wires[first.value] = call.lineno
                elif name == "_save_project_wire" and len(call.args) >= 2:
                    second = call.args[1]
                    if isinstance(second, ast.Constant) and isinstance(second.value, str):
                        saves[second.value] = call.lineno
            for node, wline in wires.items():
                if node in saves:
                    found[node] = (wline, saves[node])
        return found

    @pytest.mark.parametrize("module,nodes", list(SITES.items()))
    def test_each_project_node_saves_after_wiring(self, module, nodes):
        src = (APP / "routers" / module).read_text(encoding="utf-8")
        found = self._scan(src)
        for node in nodes:
            assert node in found, f"{module} 里 {node} 节点没有接 _save_project_wire——t3 落库被拆了？"
            wline, sline = found[node]
            assert sline > wline, f"{module}:{node} 的落库调用必须注入之后（{sline} !> {wline}）"

    def test_assembly_saves_job_wire(self):
        """章级落库必须在 _assemble_context 内（三条正文路径共用一处，结构性防漂移）。"""
        src = (APP / "routers" / "ai_factory.py").read_text(encoding="utf-8")
        for fn in ast.walk(ast.parse(src)):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == "_assemble_context":
                calls = [
                    c
                    for c in ast.walk(fn)
                    if isinstance(c, ast.Call)
                    and (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == "_save_job_wire"
                ]
                assert calls, "_assemble_context 里没有 _save_job_wire 调用"
                return
        raise AssertionError("找不到 _assemble_context")
