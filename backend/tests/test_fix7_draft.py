"""FIX-7 草稿落库与定稿体验：单文件、零真实网络、不碰真实数据库、<30 秒。

被钉死的两个线上问题（2026-10，用户现场）：
  1. 丢稿风险：前台逐章生成的正文只存在浏览器内存里——弹窗一关/页面一刷新
     就没了，用户这一章差点白花钱。修法：服务端在 `_track_active_stream`
     包装层顺手累积「已发给客户端的正文」，三条收尾路径（正常结束 / 客户端
     中断 / 报错退出）只要累积非空就写 `ai_chapter_jobs.draft_text` 并 commit；
     finalize 成功后清空（正文已入书稿，草稿使命结束）。
  2. 「点定稿没反应」：运行日志 /finalize 出现 0 次、无 4xx/5xx——点击根本
     没发请求，问题在前端交互层（禁用无提示 / 底部操作栏被 grid 布局滚出
     视口，前端部分靠人工审查 + tsc，本文件只钉服务端契约）。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_fix7_draft.py -q
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.models import AiChapterJob, AiProject, Chapter, Novel
from app.routers import ai as ai_mod
from app.routers import ai_factory as factory_mod


# ---------------------------------------------------------------- 测试替身
# （与 tests/test_sse_hardening.py 同一套口径的最小复制，独立成文避免跨测试文件耦合；
#   那边的回归测试继续守它们自己的现场，这里只守 FIX-7 的新契约。）


class FakeDB:
    """只满足被测代码用到的最小接口：get(model, pk) / commit()。"""

    def __init__(self, rows: dict):
        self.rows = rows
        self.commits = 0

    async def get(self, model, pk):  # noqa: ANN001
        row = self.rows.get(model)
        if row is not None and getattr(row, "id", None) is not None and row.id != pk:
            return None  # 主键不匹配 = 查无此行（999 之类不许摸到现有对象）
        return row

    async def commit(self):
        self.commits += 1


def make_ai_config():
    from app.models import AIConfig

    return AIConfig(
        id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
    )


def make_draft_bundle(*, draft_text=None, draft_updated_at=None, **job_over):
    """造一套（项目/任务/章节/小说）+ FakeDB；job 预置草稿字段（FIX-7 新增）。"""
    p = SimpleNamespace(
        id=1, user_id=1, status="writing", novel_id=11, chapter_llm=None, summary_llm=None,
        target_chapter_words=2000, context_recent_chapters=2, reference_novel_id=None,
        author_intent="", current_focus="", tokens_prompt=0, tokens_completion=0,
    )
    job = SimpleNamespace(
        id=7, project_id=1, chapter_id=21, status="pending", attempt=0, started_at=None,
        last_error="", last_error_code="", outline="主角入局", summary="", actual_words=0,
        finished_at=None, draft_text=draft_text, draft_updated_at=draft_updated_at,
    )
    chapter = SimpleNamespace(
        id=21, novel_id=11, volume_id=None, title="第一章 开局", sort_order=0,
        content="", word_count=0, status="draft",
    )
    novel = SimpleNamespace(id=11, title="测试之书", author="甲", genre="玄幻", description="")
    for k, v in job_over.items():
        setattr(job, k, v)
    db = FakeDB({AiChapterJob: job, Chapter: chapter, Novel: novel, AiProject: p})
    return p, job, db


def make_session_factory(db):
    """把 FakeDB 伪装成「自持会话」的 SessionLocal（与 test_sse_hardening 同口径）。"""

    class _Cm:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    return lambda: _Cm()


def fake_user():
    return SimpleNamespace(id=1, role="user")


def patch_upstream(monkeypatch, handler):
    """httpx.AsyncClient 全部换 MockTransport（零真实网络）。"""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def sse_handler(pieces=("你", "好")):
    """OpenAI 兼容 SSE 上游桩：依次吐 pieces，再 [DONE]。"""

    async def body():
        for text in pieces:
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()
        yield b"data: [DONE]\n\n"

    def handler(request):
        return httpx.Response(200, content=body())

    return handler


async def collect(iterator):
    return [chunk async for chunk in iterator]


def events_of(chunks) -> list[dict]:
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def install_generate_guards(monkeypatch, p, db, *, assemble_error=None):
    """generate_chapter 的外部依赖全部替身化（同 test_sse_hardening.install_guards）。"""

    async def _get_project(project_id, user, session):
        return p

    async def _pick_config(user, session, route_field):
        return make_ai_config()

    async def _assemble_context(project, novel, chapter, job, session):
        if assemble_error is not None:
            raise assemble_error
        return "已组装的上下文：主角入局。"

    monkeypatch.setattr(factory_mod, "_get_project", _get_project)
    monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(factory_mod, "_assemble_context", _assemble_context)
    monkeypatch.setattr(factory_mod, "SessionLocal", make_session_factory(db))


def install_finalize_guards(monkeypatch, p, db):
    """finalize_chapter 的外部依赖全部替身化（快照 / FTS / 状态文件 / 摘要）。"""

    async def _get_project(project_id, user, session):
        return p

    async def _pick_config(user, session, route_field):
        return make_ai_config()

    async def _update_state_files(*a, **k):
        return True

    async def _sync_relations_from_chapter(*a, **k):
        return None

    async def _chat_text(config, system, prompt, **kwargs):
        return "本章摘要：主角入局。"

    async def _snapshot(db, chapter, label):
        return None

    async def _sync_chapter(db, chapter_id):
        return None

    monkeypatch.setattr(factory_mod, "_get_project", _get_project)
    monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(factory_mod, "_update_state_files", _update_state_files)
    monkeypatch.setattr(factory_mod, "_sync_relations_from_chapter", _sync_relations_from_chapter)
    monkeypatch.setattr(factory_mod, "_chat_text", _chat_text)

    import app.search_fts as fts_mod
    import app.snapshot_service as snap_mod

    monkeypatch.setattr(snap_mod, "try_snapshot_before_ai", _snapshot)
    monkeypatch.setattr(fts_mod, "sync_chapter", _sync_chapter)


# ---------------------------------------------------------------- A2: 流正常结束 → 落库


class TestDraftSavedOnStreamEnd:
    def test_normal_end_saves_draft_and_stamps_time(self, monkeypatch):
        """流跑完：已发给客户端的正文必须进 draft_text，并盖 draft_updated_at。"""
        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db)
        patch_upstream(monkeypatch, sse_handler(pieces=("第一段。", "第二段。")))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        events = events_of(chunks)
        sent = "".join(e["content"] for e in events if "content" in e)
        assert sent == "第一段。第二段。", "前置确认：客户端确实收到了这两段"
        assert job.draft_text == sent, f"草稿必须与发出的正文一致，实得 {job.draft_text!r}"
        assert job.draft_updated_at is not None, "落草稿必须同时盖时间戳（/draft 端点要展示）"
        assert db.commits >= 2, "状态切换 + 草稿落库至少两次 commit"

    def test_wrapper_accumulates_only_content_frames(self, monkeypatch):
        """包装器只认 content 帧：connected/model/error/done/keepalive 都不许混进草稿。"""
        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db)
        patch_upstream(monkeypatch, sse_handler(pieces=("正", "文")))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        # connected/model/done/keepalive 帧（无 content 键）不该出现在草稿里
        assert job.draft_text == "正文"
        assert "connected" not in (job.draft_text or "")
        assert "keepalive" not in (job.draft_text or "")
        assert '"done"' in "".join(chunks), "正常收尾帧要照发（不影响客户端）"


# ---------------------------------------------------------------- A2: 客户端中断 → 部分落库


class TestDraftSavedOnDisconnect:
    def test_partial_draft_survives_client_disconnect(self, monkeypatch):
        """客户端读了一半就撤：已发出去的部分必须抢救进 draft_text（本轮的核心场景）。"""
        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db)
        patch_upstream(monkeypatch, sse_handler(pieces=("前半句。", "后半句。")))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            it = resp.body_iterator
            got = []
            async for chunk in it:
                got.append(chunk)
                if '"content"' in chunk:
                    break  # 客户端拿到第一段正文就跑路
            await it.aclose()
            return got

        got = asyncio.run(run())
        received = "".join(e["content"] for e in events_of(got) if "content" in e)
        assert received == "前半句。", f"前置确认：客户端只收到 {received!r}"
        assert job.draft_text == "前半句。", (
            f"断开后已生成的部分必须落库，实得 {job.draft_text!r}"
        )
        assert job.draft_updated_at is not None
        assert job.id not in factory_mod.ACTIVE_JOBS, "断开注销的既有契约不许回退"

    def test_disconnect_keeps_job_writing_not_failed(self, monkeypatch):
        """主动断开不是失败（既有契约）：草稿照存，但状态不许被写成 failed。"""
        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db)
        patch_upstream(monkeypatch, sse_handler(pieces=("一部分。", "另一部分。")))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            it = resp.body_iterator
            got = []
            async for chunk in it:
                got.append(chunk)
                if '"content"' in chunk:
                    break
            await it.aclose()

        asyncio.run(run())
        assert job.status == "writing", "断开不许落 failed（解锁归 /reset 与卡死扫描管）"
        assert job.draft_text == "一部分。"


# ---------------------------------------------------------------- A2: 报错退出 → 非空落库 / 空不动


class TestDraftOnErrorExit:
    def test_partial_draft_saved_when_upstream_dies_mid_stream(self, monkeypatch):
        """上游吐了一段后断线：error 事件照发、job 落 failed，但已生成的半篇要保住。"""

        def handler(request):
            async def body():
                yield 'data: {"choices":[{"delta":{"content":"写了一半。"}}]}\n\n'.encode()
                raise httpx.RemoteProtocolError("connection reset by peer")

            return httpx.Response(200, content=body())

        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db)
        patch_upstream(monkeypatch, handler)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        assert any('"error"' in c for c in chunks), "报错路径必须发 error 事件（既有契约）"
        assert job.status == "failed"
        assert job.draft_text == "写了一半。", f"半篇草稿必须落库，实得 {job.draft_text!r}"

    def test_empty_output_writes_nothing_when_no_prior_draft(self, monkeypatch):
        """准备阶段就炸（零正文）：没有旧草稿就保持 None，绝不写空串。"""
        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db, assemble_error=RuntimeError("璇玑不可达"))
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        assert any('"error"' in c for c in chunks)
        assert job.draft_text is None, f"零正文不许写草稿，实得 {job.draft_text!r}"
        assert job.draft_updated_at is None

    def test_empty_output_keeps_prior_draft_untouched(self, monkeypatch):
        """零正文时已有旧草稿必须原样保留（「空则不动」——旧稿是用户的救命稻草）。"""
        old = "上一次失败前存的草稿"
        p, job, db = make_draft_bundle(draft_text=old, draft_updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
        install_generate_guards(monkeypatch, p, db, assemble_error=RuntimeError("璇玑不可达"))
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        asyncio.run(run())
        assert job.draft_text == old, "空收尾不许清掉旧草稿"
        assert job.draft_updated_at == datetime(2026, 10, 1, tzinfo=timezone.utc), "时间戳也不许动"

    def test_wrapper_swallows_draft_save_failure(self, monkeypatch):
        """草稿写库失败不许影响流收尾（草稿是自救数据，不是主链路）。"""
        p, job, db = make_draft_bundle()
        install_generate_guards(monkeypatch, p, db)
        patch_upstream(monkeypatch, sse_handler(pieces=("正文",)))

        async def broken_save(job_id, text):
            raise RuntimeError("db 炸了")

        monkeypatch.setattr(factory_mod, "_save_job_draft", broken_save)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())  # 不抛即通过
        assert [e for e in events_of(chunks) if e.get("done")], "客户端仍要拿到完整流"


# ---------------------------------------------------------------- A3: finalize 清空草稿


class TestFinalizeClearsDraft:
    def test_finalize_success_clears_draft(self, monkeypatch):
        """定稿成功：draft_text/draft_updated_at 清空——正文已入书稿，草稿使命结束。"""
        p, job, db = make_draft_bundle(
            draft_text="即将定稿的正文", draft_updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
        job.status = "writing"
        install_finalize_guards(monkeypatch, p, db)

        data = factory_mod.FinalizeIn(content_text="这是一段足够长且合格的正文内容，已经超过了二十个字的最低门槛。")
        r = asyncio.run(factory_mod.finalize_chapter(1, 7, data, fake_user(), db))

        assert r["ok"] is True
        assert job.status == "done"
        assert job.draft_text is None, f"定稿后草稿必须清空，实得 {job.draft_text!r}"
        assert job.draft_updated_at is None

    def test_late_disconnect_save_after_finalize_is_noop(self, monkeypatch):
        """窄窗竞态守卫（天演 FIX-7 复评发现）：断连收尾的草稿落库若晚于 finalize 的
        最终 commit，不许把旧草稿盖回 done 行——正文已入书稿，草稿无意义。

        注意：这是**行为契约测试**（手工构造「先 finalize → 后落库」的顺序调用），
        不是真实并发的时序复现；真实时序里 finalize 与断连收尾在事件循环上交错，
        本测钉的是守卫本身：_save_job_draft 对 done 行必须是 no-op。
        """
        p, job, db = make_draft_bundle()
        job.status = "writing"
        install_finalize_guards(monkeypatch, p, db)
        # _save_job_draft 走自持 SessionLocal（finalize 不用它）——必须替身化到同一个
        # FakeDB，否则它会摸到真实临时库（job 不存在→静默 return），测出假绿。
        monkeypatch.setattr(factory_mod, "SessionLocal", make_session_factory(db))
        data = factory_mod.FinalizeIn(content_text="这是一段足够长且合格的正文内容，已经超过了二十个字的最低门槛。")
        r = asyncio.run(factory_mod.finalize_chapter(1, 7, data, fake_user(), db))
        assert r["ok"] is True and job.status == "done" and job.draft_text is None

        commits_before = db.commits
        # 模拟「断连收尾的草稿落库」此刻才姗姗来迟
        asyncio.run(factory_mod._save_job_draft(7, "迟到一步的断连草稿"))
        assert job.draft_text is None, f"done 行不许被迟到草稿污染，实得 {job.draft_text!r}"
        assert job.draft_updated_at is None
        assert db.commits == commits_before, "守卫命中必须是纯 no-op，不许产生 commit"


# ---------------------------------------------------------------- A4: /draft 端点


class TestDraftEndpoint:
    def test_returns_text_and_updated_at(self, monkeypatch):
        dt = datetime(2026, 10, 2, 3, 4, 5, tzinfo=timezone.utc)
        _, job, db = make_draft_bundle(draft_text="已保存的草稿正文", draft_updated_at=dt)
        monkeypatch.setattr(factory_mod, "_get_project", _always_project(db))
        out = asyncio.run(factory_mod.get_job_draft(1, 7, fake_user(), db))
        assert out["text"] == "已保存的草稿正文"
        assert out["updated_at"] == dt.isoformat()

    def test_no_draft_returns_empty_and_null(self, monkeypatch):
        _, _, db = make_draft_bundle()
        monkeypatch.setattr(factory_mod, "_get_project", _always_project(db))
        out = asyncio.run(factory_mod.get_job_draft(1, 7, fake_user(), db))
        assert out == {"text": "", "updated_at": None}, "无草稿必须返回空串+null，不许报错"

    def test_done_job_draft_returns_empty_and_null(self, monkeypatch):
        """纵深防御（天演复评顺带项）：done 行即使残留草稿也不许吐给前端——
        正文已入书稿，恢复入口应把 done 行当成「无草稿」（UI 已挡，端点焊死）。"""
        _, job, db = make_draft_bundle(draft_text="不该再见天日的旧稿")
        job.status = "done"
        monkeypatch.setattr(factory_mod, "_get_project", _always_project(db))
        out = asyncio.run(factory_mod.get_job_draft(1, 7, fake_user(), db))
        assert out == {"text": "", "updated_at": None}, f"done 行必须按无草稿返回，实得 {out}"

    def test_missing_job_is_404(self, monkeypatch):
        _, _, db = make_draft_bundle()
        monkeypatch.setattr(factory_mod, "_get_project", _always_project(db))
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.get_job_draft(1, 999, fake_user(), db))
        assert ei.value.status_code == 404

    def test_cross_project_job_is_404(self, monkeypatch):
        """别人的项目不许读草稿（越权防护）：project_id 与 job.project_id 不一致必须 404。"""
        _, job, _ = make_draft_bundle()
        p2 = SimpleNamespace(id=2, user_id=1, novel_id=11, summary_llm=None)
        db2 = FakeDB({AiProject: p2, AiChapterJob: job})
        monkeypatch.setattr(factory_mod, "_get_project", _always_project(db2))
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei2:
            asyncio.run(factory_mod.get_job_draft(2, 7, fake_user(), db2))
        assert ei2.value.status_code == 404, f"跨项目读草稿必须 404，实得 {ei2.value.status_code}"


def _always_project(db):
    async def _get_project(project_id, user, session):
        return db.rows[AiProject]

    return _get_project


# ---------------------------------------------------------------- A5: jobs 列表的 has_draft/draft_chars


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class ListDB(FakeDB):
    """按调用顺序吐 jobs / chapters / volumes /（卡死扫描查询结果）。"""

    def __init__(self, rows, results):
        super().__init__(rows)
        self.results = list(results)

    async def execute(self, stmt):
        return _Rows(self.results.pop(0) if self.results else [])


class TestJobsListDraftFields:
    def _list_jobs(self, jobs):
        p = SimpleNamespace(id=1, user_id=1, novel_id=11)
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, sort_order=0, title="开局")
        db = ListDB({AiProject: p}, [jobs, [chapter], [], []])
        return asyncio.run(factory_mod.list_jobs(1, fake_user(), db))

    def test_list_carries_has_draft_and_chars_but_not_full_text(self):
        jobs = [
            SimpleNamespace(
                id=7, project_id=1, chapter_id=21, status="done", outline="大纲", summary="",
                review_issues=None, review_score=None, actual_words=100, attempt=1,
                finished_at=None, started_at=None, last_error="", last_error_code="",
                draft_text="六个字的草稿", draft_updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            ),
            SimpleNamespace(
                id=8, project_id=1, chapter_id=21, status="pending", outline="大纲", summary="",
                review_issues=None, review_score=None, actual_words=0, attempt=0,
                finished_at=None, started_at=None, last_error="", last_error_code="",
                draft_text=None, draft_updated_at=None,
            ),
        ]
        out = self._list_jobs(jobs)
        row7 = next(r for r in out if r["id"] == 7)
        row8 = next(r for r in out if r["id"] == 8)
        assert row7["has_draft"] is True
        assert row7["draft_chars"] == 6
        assert "draft_text" not in row7, "列表绝不许带草稿全文（几十行 × 几千字直接爆包）"
        assert row8["has_draft"] is False
        assert row8["draft_chars"] == 0

    def test_legacy_row_without_draft_attrs_does_not_crash(self):
        """上线瞬间的存量行（模型列已加、行是旧替身/缓存对象）也不许让列表 500。"""

        class LegacyRow:
            """没有 draft_text/draft_updated_at 属性的旧行。"""

            def __init__(self):
                self.id = 9
                self.project_id = 1
                self.chapter_id = 21
                self.status = "writing"
                self.outline = ""
                self.summary = ""
                self.review_issues = None
                self.review_score = None
                self.actual_words = 0
                self.attempt = 0
                self.finished_at = None
                self.started_at = datetime.now(timezone.utc) - timedelta(minutes=1)

        out = self._list_jobs([LegacyRow()])
        row = out[0]
        assert row["has_draft"] is False
        assert row["draft_chars"] == 0


# ---------------------------------------------------------------- 迁移


class TestMigration:
    def test_init_db_ensures_draft_columns(self, monkeypatch):
        """init_db 必须给 ai_chapter_jobs 补 draft_text / draft_updated_at 两列（追加式迁移）。"""
        from app import db as db_mod

        executed: list[tuple[str, str]] = []

        async def fake_ensure_column(conn, table, column, definition):
            executed.append((table, column))

        monkeypatch.setattr(db_mod, "_ensure_column", fake_ensure_column)

        async def fake_migrate():
            return None

        monkeypatch.setattr(db_mod, "_migrate", fake_migrate)

        class FakeConn:
            def run_sync(self, *a, **k):
                async def noop():
                    return None

                return noop()

        class FakeCtx:
            async def __aenter__(self):
                return FakeConn()

            async def __aexit__(self, *exc):
                return False

        class FakeEngine:
            def begin(self):
                return FakeCtx()

        monkeypatch.setattr(db_mod, "engine", FakeEngine())

        class FakeResult:
            def scalar_one_or_none(self):
                return SimpleNamespace(id=1)  # 已有用户，跳过建管理员

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt):
                return FakeResult()

        monkeypatch.setattr(db_mod, "SessionLocal", lambda: FakeSession())

        asyncio.run(db_mod.init_db())
        pairs = set(executed)
        assert ("ai_chapter_jobs", "draft_text") in pairs, f"缺 draft_text 迁移，实得 {sorted(pairs)}"
        assert ("ai_chapter_jobs", "draft_updated_at") in pairs, f"缺 draft_updated_at 迁移，实得 {sorted(pairs)}"
