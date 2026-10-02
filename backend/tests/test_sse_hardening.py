"""SSE 链路加固（R1–R4）的回归测试：单文件、无网络、不碰真实数据库、<30 秒。

被钉死的故障（前端 frontend/src/lib/api.ts:154 的报错）：
「HTTP 200 + content-type text/event-stream + 收到 0 字节」→
前端提示「连接在模型返回第一个字之前被切断了……网关/代理把安静连接掐掉了」。

真实成因分四层，本文件逐层立测：
  1. 首字节被「回包前的耗时组装」拖住：ai_factory.generate_chapter 过去在
     **返回响应对象之前**先 _pick_config + _assemble_context（含璇玑 tRPC，最坏 30s）
     + db.commit()，响应头迟迟不下发 → 客户端 0 字节。
  2. 等待上游首字期间心跳太稀（KEEPALIVE_SECONDS 原为 10s）→ 静默期被代理判定空闲。
  3. SSE 响应缺抗缓冲头：nginx 类边缘默认 proxy_buffering on，会把已写出的字节攒住。
  4. `_track_active_stream(resp, job.id)` 的自引用：把 resp.body_iterator 换成
     「迭代 resp.body_iterator 自身」的生成器 → 首次 __anext__ 直接 RuntimeError
     → 响应头已发出、正文一个字节都没有，正是上面那个报错的字面现场。

上游一律用 httpx.MockTransport 打桩（零真实网络）；数据库用 FakeDB 顶替。
运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_sse_hardening.py -v
"""

import ast
import asyncio
import inspect
import json
import pathlib
import re
import time
from types import SimpleNamespace

import httpx
import pytest

from app.models import AIConfig, AiChapterJob, AiProject, Chapter, Novel
from app.routers import ai as ai_mod
from app.routers import ai_factory as factory_mod

BASE = pathlib.Path(inspect.getsourcefile(ai_mod)).parent  # .../app/routers


# ---------------------------------------------------------------- 测试替身


class FakeDB:
    """只满足被测代码用到的最小接口：get(model, pk) / commit() / rollback()。"""

    def __init__(self, rows: dict):
        self.rows = rows
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):  # noqa: ANN001
        return self.rows.get(model)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def make_bundle(**project_over):
    """造一套（项目 / 任务 / 章节 / 小说）+ FakeDB，供 generate_chapter 直接调用。"""
    p = SimpleNamespace(
        id=1, user_id=1, status="writing", novel_id=11, chapter_llm=None, summary_llm=None,
        target_chapter_words=2000, context_recent_chapters=2, reference_novel_id=None,
        author_intent="", current_focus="", tokens_prompt=0, tokens_completion=0,
    )
    job = SimpleNamespace(
        id=7, project_id=1, chapter_id=21, status="pending", attempt=0, started_at=None,
        last_error="", last_error_code="", outline="主角入局", summary="", actual_words=0,
        finished_at=None,
    )
    chapter = SimpleNamespace(
        id=21, novel_id=11, volume_id=None, title="第一章 开局", sort_order=0,
        content="<p>旧文</p>", word_count=0, status="draft",
    )
    novel = SimpleNamespace(id=11, title="测试之书", author="甲", genre="玄幻", description="")
    for k, v in project_over.items():
        setattr(p, k, v)
    db = FakeDB({AiChapterJob: job, Chapter: chapter, Novel: novel, AiProject: p})
    return p, job, db


def make_session_factory(db):
    """把现成的 FakeDB 伪装成「自持会话」的 SessionLocal（FIX-3 的替身口径）。

    被测代码 `async with SessionLocal() as s` 拿到的就是这个 db，commit 计数仍然
    落在同一个 FakeDB 上，于是「善后有没有落库」这类断言不用改口径。
    """
    sessions: list[int] = []

    class _Cm:
        async def __aenter__(self):
            sessions.append(1)
            return db

        async def __aexit__(self, *exc):
            return False

    def factory():
        return _Cm()

    factory.sessions = sessions  # type: ignore[attr-defined]
    return factory


def install_batch_pipeline(monkeypatch, *, p, job, chapter, novel, db, active_probe=None, pieces=None, hold=0.0):
    """把 batch_run「跑通一章」所需的外部依赖全部替身化（零网络、零真实落库）。

    active_probe(name) 在上下文组装时被回调，用来观察「这一章生成期间是否登记在
    ACTIVE_JOBS 里」——登记与不登记，代码看上去一模一样，只有在这里才看得出来。
    """
    from app import nightly as nightly_mod
    from app.routers import batch as batch_mod

    async def _get_project(project_id, user, session):
        return p

    async def _pick_config(user, session, route_field):
        return make_ai_config()

    async def _assemble_context(project, nv, ch, jb, session):
        if active_probe is not None:
            active_probe("assemble")
        return "已组装的上下文：主角入局。"

    async def _update_state_files(*args, **kwargs):
        return False

    async def _sync_relations_from_chapter(*args, **kwargs):
        return None

    async def _maybe_split_chapter(*args, **kwargs):
        return 1

    async def _chat_text(config, system, prompt, **kwargs):
        return "本章摘要：主角入局，风起了。"

    monkeypatch.setattr(batch_mod, "_get_project", _get_project)
    monkeypatch.setattr(batch_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(batch_mod, "_update_state_files", _update_state_files)
    monkeypatch.setattr(batch_mod, "_chat_text", _chat_text)
    monkeypatch.setattr(batch_mod, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(batch_mod, "detect", lambda text: {"score": 95, "issues": []})
    monkeypatch.setattr(factory_mod, "_assemble_context", _assemble_context)
    monkeypatch.setattr(factory_mod, "_sync_relations_from_chapter", _sync_relations_from_chapter)
    monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", _maybe_split_chapter)

    body = pieces if pieces is not None else ("风起了。" * 40,)
    patch_upstream(monkeypatch, sse_handler(pieces=body, hold=hold))
    return batch_mod


class _BatchRowResult:
    """极简 select() 结果替身：按调用顺序吐出预置的行集合。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def make_batch_db(p, job, chapter, novel):
    """给 batch_run 用的替身会话：execute 依次吐 jobs / chapters / volumes。"""

    class BatchDB(FakeDB):
        def __init__(self, rows, results):
            super().__init__(rows)
            self.results = list(results)

        async def execute(self, stmt):
            rows = self.results.pop(0) if self.results else []
            return _BatchRowResult(rows)

    return BatchDB({Novel: novel, AiProject: p, Chapter: chapter, AiChapterJob: job}, ([job], [chapter], []))


def fake_user():
    return SimpleNamespace(id=1, role="user")


def make_ai_config() -> AIConfig:
    return AIConfig(
        id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
    )


def patch_upstream(monkeypatch, handler):
    """把 httpx.AsyncClient 换成带 MockTransport 的实例（绝不产生真实网络请求）。

    返回 captured dict：记录各调用点传进来的 timeout，供「超时是否真的落到调用点」断言。
    """
    real = httpx.AsyncClient
    captured: dict = {}

    def factory(*args, **kwargs):
        # 调用方自己给了 transport（例如走 ASGITransport 的端到端用例）就尊重它，别抢
        if kwargs.get("transport") is None:
            kwargs["transport"] = httpx.MockTransport(handler)
            captured["calls"] = captured.get("calls", 0) + 1
        captured["timeout"] = kwargs.get("timeout")
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return captured


def sse_handler(pieces=("你", "好"), *, status=200, raw_body=None, hold=0.0, gaps=()):
    """OpenAI 兼容 SSE 上游桩。

    - hold：首字之前的静默秒数（模拟模型长思考）
    - pieces：要吐出的增量文本；gaps 为对应「吐这条之前再静默多少秒」
    - status != 200（或给 raw_body）时返回非流式错误响应
    """

    async def body():
        if hold:
            await asyncio.sleep(hold)
        for i, text in enumerate(pieces):
            if i < len(gaps) and gaps[i]:
                await asyncio.sleep(gaps[i])
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n\n").encode()
        yield b"data: [DONE]\n\n"

    def handler(request):
        if status != 200 or raw_body is not None:
            return httpx.Response(status, content=(raw_body or b'{"error":"boom"}'))
        return httpx.Response(200, content=body())

    return handler


def empty_handler():
    """上游 200 却零增量（推理模型只回 reasoning_content 时就是这样）。"""

    def handler(request):
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    return handler


async def collect(iterator):
    return [chunk async for chunk in iterator]


async def _collect_response(coro):
    """await 一个「返回响应对象」的端点协程，再把流全部收下来。"""
    resp = await coro
    return await collect(resp.body_iterator)


async def _drain(iterator):
    async for _ in iterator:
        pass


def events_of(chunks) -> list[dict]:
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


def install_guards(monkeypatch, p, db=None, *, assemble_delay=0.0, assemble_error=None, calls=None, pick_delay=0.0):
    """把 generate_chapter 依赖的外部零件（项目查询 / 选模型 / 组装上下文）换成替身。"""

    async def _get_project(project_id, user, db):
        return p

    async def _pick_config(user, db, route_field):
        if calls is not None:
            calls.append("pick_config")
        if pick_delay:
            await asyncio.sleep(pick_delay)
        return make_ai_config()

    async def _assemble_context(project, novel, chapter, job, db):
        if calls is not None:
            calls.append("assemble")
        if assemble_error is not None:
            raise assemble_error
        if assemble_delay:
            await asyncio.sleep(assemble_delay)
        return "已组装的上下文：主角入局。"

    monkeypatch.setattr(factory_mod, "_get_project", _get_project)
    monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(factory_mod, "_assemble_context", _assemble_context)
    if db is not None:
        # FIX-3：_prepare/_on_error 自持会话（不再蹭请求级 db），测试里让它回到同一个替身
        monkeypatch.setattr(factory_mod, "SessionLocal", make_session_factory(db))


# ---------------------------------------------------------------- 1. 首字节先于组装


class TestFirstByteBeforeAssembly:
    def test_connected_arrives_while_assembly_still_sleeping(self, monkeypatch):
        """组装要 3 秒，首字节必须 <1 秒到手——这就是「先回包、再干活」。"""
        p, job, db = make_bundle()
        calls: list[str] = []
        install_guards(monkeypatch, p, db=db, assemble_delay=3.0, calls=calls)
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            started = time.monotonic()
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            it = resp.body_iterator
            first = await asyncio.wait_for(it.__anext__(), timeout=1.0)
            elapsed = time.monotonic() - started
            # 让 prepare 真的跑起来，再用「取消上层任务」的方式断开——
            # 这正是 uvicorn/starlette 处理客户端断开的姿势（直接 aclose 只会停在 yield 处）
            drain = asyncio.create_task(_drain(it))
            await asyncio.sleep(0.05)
            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass
            return first, elapsed

        first, elapsed = asyncio.run(run())
        assert elapsed < 1.0, f"首字节耗时 {elapsed:.2f}s：昂贵组装仍在回包前把请求拖住了"
        assert events_of([first])[0]["stage"] == "connected"
        assert "assemble" in calls, "组装应改为在流内发起"
        assert job.id not in factory_mod.ACTIVE_JOBS, "断开后登记要注销"

    def test_expensive_config_pick_also_moved_into_stream(self, monkeypatch):
        """模型路由解析（_pick_config）同样不许挡在首字节前面。"""
        p, _, db = make_bundle()
        install_guards(monkeypatch, p, db=db, pick_delay=3.0)
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            started = time.monotonic()
            first = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=1.0)
            return first, time.monotonic() - started

        first, elapsed = asyncio.run(run())
        assert elapsed < 1.0, f"_pick_config 仍在回包前：{elapsed:.2f}s"

    def test_full_stream_order_and_job_lifecycle(self, monkeypatch):
        """完整一条流：connected → content → done；期间 job 转 writing 并落库。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db, assemble_delay=0.2)
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return resp, await collect(resp.body_iterator)

        resp, chunks = asyncio.run(run())
        events = events_of(chunks)
        assert events[0]["stage"] == "connected", "第一条事件必须是 connected"
        assert [e.get("content") for e in events if "content" in e] == ["你", "好"]
        assert [e for e in events if e.get("done")], "正常收尾必须有 done"
        assert not [e for e in events if "error" in e]
        assert job.status == "writing" and job.attempt == 1
        assert db.commits >= 1, "状态切换必须落库"
        assert job.id not in factory_mod.ACTIVE_JOBS, "流结束后必须注销"

    def test_cheap_guards_still_return_http_errors(self, monkeypatch):
        """守卫不能被搬进流里：状态不对 / 任务不存在仍是 400 / 404（不是 SSE 事件）。"""
        from fastapi import HTTPException

        p, _, db = make_bundle(status="outline")
        install_guards(monkeypatch, p, db=db)
        with pytest.raises(HTTPException) as ei:
            asyncio.run(factory_mod.generate_chapter(1, 7, None, fake_user(), db))
        assert ei.value.status_code == 400

        p2, _, db2 = make_bundle()
        install_guards(monkeypatch, p2, db=db2)
        db2.rows[AiChapterJob] = None
        with pytest.raises(HTTPException) as ei2:
            asyncio.run(factory_mod.generate_chapter(1, 7, None, fake_user(), db2))
        assert ei2.value.status_code == 404

    def test_assembly_failure_does_not_leave_job_writing(self, monkeypatch):
        """组装炸了：必须发 error 事件，且 job 绝不能永远卡在 writing。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db, assemble_error=RuntimeError("璇玑不可达"))
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        errors = [e["error"] for e in events_of(chunks) if "error" in e]
        assert errors, "组装失败必须发 error 事件"
        assert "璇玑不可达" in errors[0]
        assert job.status == "failed", f"job 不能停在 {job.status}"
        assert job.last_error and db.commits >= 1

    def test_upstream_error_marks_job_failed(self, monkeypatch):
        """上游 500：error 事件 + job 回退（不能只靠前端回写 /fail）。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, sse_handler(status=500, raw_body=b'{"error":"quota exceeded"}'))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        events = events_of(chunks)
        assert events[0]["stage"] == "connected", "报错前首字节仍要先出去"
        errors = [e["error"] for e in events if "error" in e]
        assert errors and "500" in errors[0]
        assert job.status == "failed"
        assert job.last_error_code == "quota", f"失败应可归因，实得 {job.last_error_code}"

    def test_zero_output_upstream_marks_job_failed(self, monkeypatch):
        """上游 200 零增量：同样要 error 事件 + job 善后。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, empty_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        errors = [e["error"] for e in events_of(chunks) if "error" in e]
        assert errors and "空内容" in errors[0]
        assert job.status == "failed"

    def test_client_disconnect_unregisters_active_job(self, monkeypatch):
        """客户端断开（aclose）要注销登记，否则自动解锁会永远跳过这一章。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db, assemble_delay=2.0)
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            it = resp.body_iterator
            first = await it.__anext__()
            during = job.id in factory_mod.ACTIVE_JOBS
            await it.aclose()
            return first, during

        first, during = asyncio.run(run())
        assert events_of([first])[0]["stage"] == "connected"
        assert during, "流式期间必须处于登记状态"
        assert job.id not in factory_mod.ACTIVE_JOBS, "断开后必须注销"


class TestErrorExitSettlesBeforeYield:
    """错误出口的善后必须发生在 **yield 之前**（FIX-1）。

    生成器在 yield 处挂起，只有消费者再拉一次才会往下走。若把
    `await notify_error(...)` 放在 `yield error_chunk(...)` 之后，那么客户端读到
    error 事件就撤（或网关 send 失败 / 上层 aclose）时，GeneratorExit 正好在 yield
    处抛出 → 善后被跳过 → job 永久停在 writing、last_error 为空。天演的 ASGI 探针
    实测：客户端收到 connected+error 两个事件，但 job.status='writing'、
    last_error=''、commits=1 —— 这是「本次要治的场景」里最隐蔽的一种。
    """

    async def _quit_on_error(self, resp):
        """读流，一看到 error 事件就当作客户端撤走（不再往下拉 + aclose）。"""
        it = resp.body_iterator
        seen: list[str] = []
        async for chunk in it:
            seen.append(chunk)
            if '"error"' in chunk:
                break
        await it.aclose()
        return seen

    def _assert_settled(self, job, db, keyword: str, min_commits: int = 2) -> None:
        """min_commits：炸在准备阶段的路径只有善后那 1 次 commit；进过 writing 的是 2 次。"""
        assert job.status == "failed", f"error 事件都发出去了，善后却没跑（status={job.status}）"
        assert job.last_error and keyword in job.last_error, f"last_error 应记录失败原因：{job.last_error!r}"
        assert db.commits >= min_commits, f"善后必须已 commit（实际 {db.commits}），否则前端刷新回来还是 writing"

    def test_prepare_failure_settles_even_if_client_quits_immediately(self, monkeypatch):
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db, assemble_error=RuntimeError("璇玑召回超时"))
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await self._quit_on_error(resp)

        seen = asyncio.run(run())
        assert any('"error"' in c for c in seen), "必须把错误发给前端"
        self._assert_settled(job, db, "璇玑召回超时", min_commits=1)

    def test_upstream_fatal_settles_even_if_client_quits_immediately(self, monkeypatch):
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, sse_handler(status=502, raw_body=b'{"error":"overloaded"}'))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await self._quit_on_error(resp)

        seen = asyncio.run(run())
        assert any('"error"' in c for c in seen)
        self._assert_settled(job, db, "502")

    def test_zero_output_settles_even_if_client_quits_immediately(self, monkeypatch):
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, empty_handler())

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await self._quit_on_error(resp)

        seen = asyncio.run(run())
        assert any('"error"' in c for c in seen)
        self._assert_settled(job, db, "空内容")

    def test_missing_config_branch_settles_before_event(self):
        """连配置/消息都没备齐的那条出口：同样不许「先发事件再善后」。"""
        fired: list[str] = []

        async def on_error(message: str) -> None:
            fired.append(message)

        async def run():
            resp = await ai_mod._stream_openai(None, None, on_error=on_error)
            return await self._quit_on_error(resp)

        seen = asyncio.run(run())
        assert any('"error"' in c for c in seen)
        assert fired, "on_error 必须在 error 事件发出去之前就跑完"

    def test_client_stop_mid_stream_is_not_recorded_as_failure(self, monkeypatch):
        """反向约束：用户点停止（无错误的主动断开）不许被写成失败。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, sse_handler(pieces=("整章正文",), hold=5.0))
        monkeypatch.setattr(ai_mod, "KEEPALIVE_SECONDS", 0.05)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            it = resp.body_iterator
            got = []
            async for chunk in it:
                got.append(chunk)
                if len(got) >= 3:
                    break
            await it.aclose()
            return got

        asyncio.run(run())
        assert job.status == "writing", "主动断开不是失败：不许落 failed"
        assert job.last_error == "", "不许伪造失败原因"
        assert job.id not in factory_mod.ACTIVE_JOBS


class TestSelfReferenceRegression:
    """线上真凶的回归钉：_track_active_stream 绝不能去迭代「它自己」。

    Zeabur 运行日志（2026-09-30T14:22:39Z）：
        RuntimeError: anext(): asynchronous generator is already running
          File "/app/app/routers/ai_factory.py", line 1196, in _track_active_stream
            async for chunk in inner.body_iterator
    因为调用点写的是 `_track_active_stream(resp, job.id)`：inner 就是 resp，而
    resp.body_iterator 此刻已经被换成这个包装器本身 → 首次 anext 就抛错 →
    一个字节都不产出 → 客户端拿到「HTTP 200 + text/event-stream + 0 字节」，
    也就是前端 api.ts:154 的报错。**逐章生成 100% 失败，与模型无关**
    （这解释了「换了很多模型都一样」）。
    """

    def test_wrapper_path_yields_upstream_chunks(self, monkeypatch):
        """端点的包装路径必须真的把上游 chunk 发出去（修复前：0 个 chunk）。"""
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, sse_handler(pieces=("第一句。", "第二句。")))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        chunks = asyncio.run(run())
        assert chunks, "包装层一字节都不发 = 线上 0 字节事故"
        events = events_of(chunks)
        assert events[0]["stage"] == "connected"
        assert "".join(e["content"] for e in events if "content" in e) == "第一句。第二句。"
        assert events[-1].get("done") is True
        assert job.id not in factory_mod.ACTIVE_JOBS

    def test_passing_the_response_object_is_rejected(self):
        """FIX-4.2：getattr 兼容分支删掉了——传响应对象必须**当场报错**，不悄悄兜住。

        过去这条用例钉的是「传 resp 也能流出字节」（靠 getattr 兜底）。评审说得对：
        把写错的调用伪装成正常代码，代价是下一个人照样写错，而下一次改动稍不留神
        就又回到 0 字节。现在契约收窄成「只收迭代器」，误用在调用那一刻就炸。
        """
        from app.sse import sse_streaming

        async def inner_gen():
            yield b"data: a\n\n"
            yield b"data: b\n\n"

        resp = sse_streaming(inner_gen())
        with pytest.raises(TypeError) as ei:
            factory_mod._track_active_stream(resp, 9001)
        assert "body_iterator" in str(ei.value), "错误信息要直接说清该传什么"
        assert 9001 not in factory_mod.ACTIVE_JOBS

    def test_tracker_registers_during_stream_and_clears_on_normal_end(self):
        async def inner_gen():
            yield "x"
            yield "y"

        async def run():
            seen = []
            async for chunk in factory_mod._track_active_stream(inner_gen(), 9002):
                seen.append((chunk, 9002 in factory_mod.ACTIVE_JOBS))
            return seen

        seen = asyncio.run(run())
        assert [c for c, _ in seen] == ["x", "y"]
        assert all(active for _, active in seen), "流式期间必须处于登记状态"
        assert 9002 not in factory_mod.ACTIVE_JOBS, "正常结束必须注销"

    def test_tracker_clears_on_midstream_error(self):
        async def bad_gen():
            yield "x"
            raise RuntimeError("上游炸了")

        async def run():
            try:
                async for _ in factory_mod._track_active_stream(bad_gen(), 9003):
                    pass
            except RuntimeError:
                return "raised"
            return "swallowed"

        assert asyncio.run(run()) == "raised", "错误必须继续外抛，包装层不许吞"
        assert 9003 not in factory_mod.ACTIVE_JOBS, "异常结束同样必须注销"

    def test_disconnect_leaves_job_catchable_by_stuck_sweep(self, monkeypatch):
        """客户端断开时不改写 job（那是 finalize / 解锁接口的职责），但必须注销登记，
        好让卡死扫描（STUCK_MINUTES）能把它捞回来——否则界面永远转圈在 writing。"""
        from datetime import datetime, timedelta, timezone

        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, sse_handler(pieces=("半句",), hold=5.0))
        monkeypatch.setattr(ai_mod, "KEEPALIVE_SECONDS", 0.05)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            it = resp.body_iterator
            got = []
            async for chunk in it:
                got.append(chunk)
                if len(got) >= 2:
                    break
            await it.aclose()  # 客户端跑路
            return got

        got = asyncio.run(run())
        assert len(got) >= 2
        assert job.status == "writing", "prepare 已执行：任务应已进入 writing"
        assert job.started_at is not None
        assert job.id not in factory_mod.ACTIVE_JOBS, "断开后必须注销，否则扫描永远跳过它"
        now = datetime.now(timezone.utc)
        assert factory_mod.job_is_stuck(job.status, job.started_at, now) is False
        later = now + timedelta(minutes=factory_mod.STUCK_MINUTES + 1)
        assert factory_mod.job_is_stuck(job.status, job.started_at, later) is True, "超时后必须可被自动解锁捞回"


    def test_call_site_contract_passes_iterator_not_response(self):
        """源码契约（防回退）：调用点先取出 inner 再换包，不许把 resp 传回去。

        注：`_track_active_stream` 现在在调用时刻就取定内层流，即使误传 resp 也不会
        再自引用；这条断言守的是**可读性与同款写法不再扩散**，不是最后一道防线。
        用 AST 扫（注释与 docstring 里那些"反面教材"不该被文本匹配误伤）。
        """
        import ast

        src = (BASE / "ai_factory.py").read_text(encoding="utf-8")
        calls = [
            node
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_track_active_stream"
        ]
        assert calls, "ai_factory.py 里已经没有包装调用点了？"
        for call in calls:
            first = call.args[0] if call.args else None
            assert not (isinstance(first, ast.Name) and first.id == "resp"), (
                f"第 {call.lineno} 行又把响应对象传进包装器（会自引用）"
            )
            assert isinstance(first, ast.Name) and first.id == "inner", (
                f"第 {call.lineno} 行应传换包前取出的 inner 迭代器"
            )
        assert re.search(r"inner = resp\.body_iterator", src), "缺「先取出原迭代器再替换」这一步"

        # 全仓自查：包装点只有 ai_factory 一处，别处不许再出现 body_iterator 换包写法
        for name in ("batch.py", "ai_import.py", "ai.py"):
            assert "body_iterator" not in (BASE / name).read_text(encoding="utf-8"), f"{name} 出现了 body_iterator 换包"


class TestTrackerClosesInnerStream:
    """断开时必须把**内层**流一起关掉（第 1 轮踩过：不关 → pump 任务残留 20s 挂起）。"""

    def test_inner_stream_is_closed_when_client_disconnects(self):
        closed: list[str] = []

        async def inner():
            try:
                yield "a"
                yield "b"
            finally:
                closed.append("inner")

        async def run():
            it = factory_mod._track_active_stream(inner(), 9301)
            first = await it.__anext__()
            await it.aclose()  # 相当于客户端读到一半就撤
            # 必须在 aclose 返回时就关完：等 GC / loop 的 async-generator 终结器
            # 是不可靠的（服务端事件循环不会关，那个正等上游的 pump 任务就一直挂着）
            return first, list(closed)

        first, closed_at_close = asyncio.run(run())
        assert first == "a"
        assert closed_at_close == ["inner"], f"内层流没被同步关掉，它的 pump 任务还在等上游：{closed_at_close}"
        assert 9301 not in factory_mod.ACTIVE_JOBS


class TestEndToEndOverAsgi:
    """真走一遍 ASGI 传输：响应头与事件顺序在完整链路上成立，而不只成立于生成器。"""

    def test_headers_and_event_order_over_asgi(self, monkeypatch):
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db, assemble_delay=0.35)
        patch_upstream(monkeypatch, sse_handler(pieces=("风起了。",), hold=0.3))
        monkeypatch.setattr(ai_mod, "KEEPALIVE_SECONDS", 0.1)  # 心跳调密，便于在 1 秒内看到多条

        from fastapi import FastAPI

        probe = FastAPI()

        @probe.post("/gen")
        async def _route():
            return await factory_mod.generate_chapter(1, 7, None, fake_user(), db)

        async def run():
            lines: list[str] = []
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=probe), base_url="http://probe"
            ) as client:
                async with client.stream("POST", "/gen") as resp:
                    hdrs = dict(resp.headers)
                    code = resp.status_code
                    async for line in resp.aiter_lines():
                        if line.strip():
                            lines.append(line)
            return code, hdrs, lines

        code, hdrs, lines = asyncio.run(run())
        assert code == 200
        assert hdrs.get("x-accel-buffering") == "no", f"抗缓冲头没下发到线上：{hdrs}"
        assert "no-cache" in hdrs.get("cache-control", "")
        assert hdrs.get("content-type", "").startswith("text/event-stream")
        text = "\n".join(lines)
        assert lines[0].startswith("data:") and '"connected"' in lines[0], "第一条事件必须是 connected"
        assert text.index(": keepalive") < text.index("风起了"), "静默期心跳要早于正文"
        assert '"done"' in text, "正文出来后必须正常收尾"
        assert '"error"' not in text


# ---------------------------------------------------------------- 2. 静默期有心跳


class TestKeepaliveDuringSilence:
    def test_keepalive_during_upstream_silence(self, monkeypatch):
        """上游 1 秒不吐数据：这 1 秒里客户端必须持续收到 : keepalive，之后正文照常出来。"""
        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "0.2")
        patch_upstream(monkeypatch, sse_handler(pieces=("你好世界",), hold=1.0))

        async def run():
            resp = await ai_mod._stream_openai(make_ai_config(), [{"role": "user", "content": "写"}])
            started = time.monotonic()
            return resp, [(round(time.monotonic() - started, 2), c) async for c in resp.body_iterator]

        resp, stamps = asyncio.run(run())
        text = "".join(c for _, c in stamps)
        assert ": keepalive" in text, "静默期必须有心跳"
        first_content_at = next(t for t, c in stamps if '"content"' in c)
        keepalives_before = [c for t, c in stamps if ": keepalive" in c and t < first_content_at]
        assert len(keepalives_before) >= 2, f"1 秒静默里只发了 {len(keepalives_before)} 次心跳"
        events = events_of([c for _, c in stamps])
        assert [e.get("content") for e in events if "content" in e] == ["你好世界"]
        assert [e for e in events if e.get("done")], "心跳不能打断正常正文"

    def test_keepalive_default_5s_and_env_overridable(self, monkeypatch):
        from app import llm_timeouts

        assert llm_timeouts.KEEPALIVE_SECONDS <= 5.0, "默认心跳必须 ≤5s（原 10s 太稀）"
        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "1.5")
        assert llm_timeouts.keepalive_seconds() == 1.5, "环境变量应能在运行期覆盖心跳"
        monkeypatch.setattr(ai_mod, "KEEPALIVE_SECONDS", 0.3)
        assert llm_timeouts.keepalive_seconds(ai_mod.KEEPALIVE_SECONDS) == 0.3, "模块级显式覆盖优先级最高"


# ---------------------------------------------------------------- 3. 抗缓冲响应头


async def _tiny_gen():
    yield ": hi\n\n"


class TestPumpTaskIsReaped:
    """心跳骨架收尾：断开后不许留下「还在等上游」的任务。"""

    def test_disconnect_cancels_the_pump_task(self, monkeypatch):
        """只 cancel 不等待 → 上游那条读请求（超时 15 分钟）继续占连接与配额。

        这条断言针对 app/sse.paced_upstream 的收尾：cancel 之后必须真的等它落地，
        而不是把清理时机交给不确定的调度（第 1 轮就踩过 20s 挂起）。
        """
        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "0.15")
        patch_upstream(monkeypatch, sse_handler(pieces=("正文",), hold=30.0))  # 上游 30s 一个字都不吐

        async def run():
            resp = await ai_mod._stream_openai(make_ai_config(), [{"role": "user", "content": "hi"}])
            it = resp.body_iterator
            seen: list[str] = []
            async for chunk in it:
                seen.append(chunk)
                if ": keepalive" in chunk:
                    break
            await it.aclose()
            leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
            return seen, [str(t.get_coro()) for t in leftover]

        seen, leftover = asyncio.run(run())
        assert any(": keepalive" in c for c in seen), f"没等到心跳帧：{seen}"
        assert not leftover, f"断开后仍有 {len(leftover)} 个任务在等上游：{leftover}"


class TestSseHeaders:
    def test_stream_openai_response_has_antibuffering_headers(self, monkeypatch):
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            return await ai_mod._stream_openai(make_ai_config(), [{"role": "user", "content": "写"}])

        resp = asyncio.run(run())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["x-accel-buffering"] == "no"
        assert "no-cache" in resp.headers["cache-control"]

    def test_generate_chapter_response_inherits_headers(self, monkeypatch):
        """generate_chapter 复用 _stream_openai 的响应 → 自动带上抗缓冲头。"""
        p, _, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, sse_handler())

        async def run():
            return await factory_mod.generate_chapter(1, 7, None, fake_user(), db)

        resp = asyncio.run(run())
        assert resp.headers["x-accel-buffering"] == "no"

    def test_shared_helper_constructs_and_is_reused(self):
        """三处 SSE 必须走同一个共享构造器（防再有人裸写 StreamingResponse）。"""
        from app.sse import SSE_HEADERS, sse_streaming

        resp = sse_streaming(_tiny_gen())
        assert resp.media_type == "text/event-stream"
        assert resp.headers["x-accel-buffering"] == "no"
        assert "no-cache" in resp.headers["cache-control"]
        assert SSE_HEADERS["X-Accel-Buffering"] == "no"

        for name in ("ai", "batch", "ai_import"):
            module = __import__(f"app.routers.{name}", fromlist=["*"])
            assert module.sse_streaming is sse_streaming, f"routers/{name}.py 没接上共享构造器"
            src = inspect.getsource(module)
            assert "sse_streaming(" in src, f"routers/{name}.py 仍在裸写 SSE 响应"
            assert 'StreamingResponse(generate(), media_type="text/event-stream")' not in src

    def test_batch_run_response_has_antibuffering_headers(self, monkeypatch):
        """批量连跑的 SSE 出口：真跑到 return 那一行（生成器不迭代，零 LLM 调用）。"""
        from app.routers import batch as batch_mod

        p = SimpleNamespace(id=1, status="writing", novel_id=11, chapter_llm=None, summary_llm=None)
        job = SimpleNamespace(id=7, project_id=1, chapter_id=21, status="pending", outline="", attempt=0)
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="第一章", sort_order=0, content="<p>旧</p>")
        novel = SimpleNamespace(id=11, title="书", user_id=1)

        class DB(FakeDB):
            def __init__(self, rows, results):
                super().__init__(rows)
                self.results = list(results)

            async def execute(self, stmt):  # 依次吐出 jobs / chapters / volumes
                rows = self.results.pop(0) if self.results else []

                class R:
                    def scalars(self):
                        return self

                    @staticmethod
                    def all():
                        return rows

                    def __iter__(self):
                        return iter(rows)

                return R()

        db = DB({Novel: novel}, ([job], [chapter], []))

        async def _get_project(project_id, user, session):
            return p

        async def _pick_config(user, session, route_field):
            return make_ai_config()

        monkeypatch.setattr(batch_mod, "_get_project", _get_project)
        monkeypatch.setattr(batch_mod, "_pick_config", _pick_config)

        resp = asyncio.run(batch_mod.batch_run(1, 1, fake_user(), db))
        assert resp.media_type == "text/event-stream"
        assert resp.headers["x-accel-buffering"] == "no"
        assert "no-cache" in resp.headers["cache-control"]

    def test_import_endpoint_response_has_antibuffering_headers(self):
        """导入逆向工程的 SSE 出口：建项目/写库都在生成器里，替身 db 足以跑到 return。"""
        from app.routers import ai_import as import_mod

        text = "第一章 开局\n" + ("主角睁开眼，四周一片漆黑。" * 40) + "\n\n第二章 启程\n" + ("他走出山门。" * 40)
        data = import_mod.ImportIn(title="导入测试", genre="玄幻", text=text)
        resp = asyncio.run(import_mod.import_novel(data, fake_user(), FakeDB({})))
        assert resp.media_type == "text/event-stream"
        assert resp.headers["x-accel-buffering"] == "no"
        assert "no-cache" in resp.headers["cache-control"]


# ---------------------------------------------------------------- 4. 超时配置


class TestTimeoutConfig:
    def test_defaults_are_generous(self):
        from app import llm_timeouts

        assert llm_timeouts.STREAM_READ_SECONDS >= 600
        assert llm_timeouts.JSON_SECONDS >= 600
        assert llm_timeouts.AUX_SECONDS >= 600
        assert llm_timeouts.KEEPALIVE_SECONDS <= 5

    def test_env_overrides_and_safety(self, monkeypatch):
        from app import llm_timeouts

        monkeypatch.setenv("BEIDOU_LLM_STREAM_READ_SECONDS", "1200")
        assert llm_timeouts.stream_read_seconds() == 1200
        monkeypatch.setenv("BEIDOU_LLM_STREAM_READ_SECONDS", "abc")
        assert llm_timeouts.stream_read_seconds() == llm_timeouts.STREAM_READ_SECONDS, "脏值必须回落默认"

    def test_openai_timeout_shape(self):
        from app import llm_timeouts

        t = llm_timeouts.openai_timeout(900)
        assert t.read == 900 and t.write == 900 and t.pool == 900
        assert t.connect == 15.0
        assert llm_timeouts.openai_timeout(900, connect=3.0).connect == 3.0

    @pytest.mark.parametrize(
        "dirty",
        ["", "   ", "0", "-1", "abc", "nan", "inf", "1e999", "900.5.5", "86401", "99999999999"],
    )
    def test_bad_env_values_fall_back_to_default(self, monkeypatch, dirty):
        """一个写错的环境变量不许把生成打成秒断：脏值一律回落默认。

        覆盖空 / 空白 / 0 / 负数 / 非数字 / inf / nan / 超 24 小时（多半是有人把
        毫秒当秒写）。0 是最阴险的一种——「超时 0 秒」= 每次请求立刻失败。
        """
        from app import llm_timeouts

        for var, resolver, default in (
            ("BEIDOU_LLM_STREAM_READ_SECONDS", llm_timeouts.stream_read_seconds, llm_timeouts.STREAM_READ_SECONDS),
            ("BEIDOU_LLM_JSON_SECONDS", llm_timeouts.json_seconds, llm_timeouts.JSON_SECONDS),
            ("BEIDOU_LLM_AUX_SECONDS", llm_timeouts.aux_seconds, llm_timeouts.AUX_SECONDS),
            ("BEIDOU_KEEPALIVE_SECONDS", llm_timeouts.keepalive_seconds, llm_timeouts.KEEPALIVE_SECONDS),
        ):
            monkeypatch.setenv(var, dirty)
            assert resolver() == default, f"{var}={dirty!r} 必须回落默认，而不是生效或抛错"

    def test_timeout_never_becomes_zero_or_negative_even_with_bad_override(self):
        """override 传脏值同样不许把心跳变成 0（0 = 忙轮询，100% CPU）。"""
        from app import llm_timeouts

        for bad in (0, -3, None, "abc", float("nan"), float("inf")):
            assert llm_timeouts.keepalive_seconds(bad) == llm_timeouts.KEEPALIVE_SECONDS
        assert llm_timeouts.openai_timeout(0).read == llm_timeouts.STREAM_READ_SECONDS
        assert llm_timeouts.openai_timeout("abc").read == llm_timeouts.STREAM_READ_SECONDS

    def test_stream_upstream_uses_stream_read_timeout(self, monkeypatch):
        captured = patch_upstream(monkeypatch, sse_handler())

        async def run():
            resp = await ai_mod._stream_openai(make_ai_config(), [{"role": "user", "content": "写"}])
            return await collect(resp.body_iterator)

        asyncio.run(run())
        from app import llm_timeouts

        assert captured["timeout"].read == llm_timeouts.STREAM_READ_SECONDS >= 600, "流式读超时必须真落到调用点"

    def test_factory_json_call_uses_json_timeout(self, monkeypatch):
        from app import llm_timeouts

        def handler(request):
            return httpx.Response(
                200, content=json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
            )

        captured = patch_upstream(monkeypatch, handler)
        text = asyncio.run(factory_mod._chat_text(make_ai_config(), "sys", "user"))
        assert text == '{"ok":true}'
        assert captured["timeout"].read == llm_timeouts.JSON_SECONDS >= 600


class TestStuckThresholdFollowsTimeouts:
    """FIX-2：卡死阈值必须跟随超时口径，登记必须覆盖 batch / nightly。

    流式读超时放宽到 15 分钟后，硬编码 15 分钟的阈值与它同值：正常在跑的长章节
    会被 _sweep_stuck_jobs 当成尸体捞回，与仍在写这行的流互踩状态。
    """

    def test_stuck_minutes_helper_exists_and_is_generous(self):
        from app import llm_timeouts

        assert hasattr(llm_timeouts, "stuck_minutes"), "缺 llm_timeouts.stuck_minutes()：阈值必须跟随超时口径"
        assert llm_timeouts.stuck_minutes() >= 30, "阈值必须明显大于单章最坏耗时，否则长章节会被误判卡死"

    def test_stuck_minutes_env_override_and_bad_value(self, monkeypatch):
        from app import llm_timeouts

        monkeypatch.setenv("BEIDOU_STUCK_MINUTES", "45")
        assert llm_timeouts.stuck_minutes() == 45
        monkeypatch.setenv("BEIDOU_STUCK_MINUTES", "abc")
        assert llm_timeouts.stuck_minutes() >= 30, "脏值必须回落默认，不能变成 0 或 None"

    def test_module_constant_agrees_with_helper(self):
        from app import llm_timeouts
        from app.routers.ai_factory import STUCK_MINUTES

        assert STUCK_MINUTES == llm_timeouts.stuck_minutes(), "常量与解析器口径不一致：解锁文案里的分钟数会骗人"

    def test_default_threshold_no_longer_flags_a_twenty_minute_run(self):
        from datetime import datetime, timedelta, timezone

        from app import llm_timeouts
        from app.routers.ai_factory import job_is_stuck

        now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        assert job_is_stuck("writing", now - timedelta(minutes=20), now) is False, "20 分钟仍在合理生成窗口内"
        assert job_is_stuck("writing", now - timedelta(minutes=llm_timeouts.stuck_minutes() + 1), now) is True

    def test_legacy_row_without_started_at_is_catchable(self):
        """注释与实现必须一致：writing 且 started_at 为空（老数据/崩溃残留）算可疑。

        过去 job_is_stuck 对 None 直接 return False，而 _sweep_stuck_jobs 的注释说
        「算作卡死」——两边相反，后果是这批行永远解不了锁，界面一直转圈。
        """
        from datetime import datetime, timezone

        from app.routers.ai_factory import job_is_stuck

        now = datetime.now(timezone.utc)
        assert job_is_stuck("writing", None, now) is True, "无时间戳的 writing 行必须能被捞回"
        assert job_is_stuck("writing", None, now, active=True) is False, "本进程正在跑的任务永远不算卡死"
        assert job_is_stuck("pending", None, now) is False, "只有 writing 才可能被判定卡死"


class TestTrackActiveJobHelper:
    """FIX-2.3：把「登记/注销」从流包装里泛化出来，batch / nightly 也能用同一件。"""

    def test_context_manager_registers_and_clears(self):
        async def run():
            seen = []
            async with factory_mod.track_active_job(9101):
                seen.append(9101 in factory_mod.ACTIVE_JOBS)
            return seen

        assert asyncio.run(run()) == [True]
        assert 9101 not in factory_mod.ACTIVE_JOBS

    def test_context_manager_clears_on_exception(self):
        async def run():
            try:
                async with factory_mod.track_active_job(9102):
                    raise RuntimeError("炸在半路")
            except RuntimeError:
                return 9102 in factory_mod.ACTIVE_JOBS
            raise AssertionError("异常必须外抛")

        assert asyncio.run(run()) is False, "异常路径也要注销，否则该任务永远不参与卡死判定"


class TestBatchChapterLifecycle:
    """batch 连跑的一章：时间戳、登记、静默期心跳，三样都得有。"""

    def _run_one_chapter(self, monkeypatch, *, hold=0.0, pieces=None):
        p, job, bundle = make_bundle()
        chapter, novel = bundle.rows[Chapter], bundle.rows[Novel]
        db = make_batch_db(p, job, chapter, novel)
        seen_active: list[str] = []

        def probe(name: str) -> None:
            seen_active.append(f"{name}:{job.id in factory_mod.ACTIVE_JOBS}")

        batch_mod = install_batch_pipeline(
            monkeypatch, p=p, job=job, chapter=chapter, novel=novel, db=db,
            active_probe=probe, pieces=pieces, hold=hold,
        )
        chunks = asyncio.run(_collect_response(batch_mod.batch_run(1, 1, fake_user(), db)))
        return p, job, db, chunks, seen_active

    def test_batch_writes_started_at_when_entering_writing(self, monkeypatch):
        """batch 过去只写 status/attempt，从不写 started_at → 陈旧时间戳一进
        writing 就早已超过阈值，用户下次打开面板（GET jobs 触发 _sweep_stuck_jobs）
        就被判卡死、写回 pending 并盖一句「生成中断」，而流还在跑 → 两个写入方互踩。"""
        from datetime import datetime, timezone

        from app.routers.ai_factory import job_is_stuck

        p, job, db, chunks, seen = self._run_one_chapter(monkeypatch)
        assert job.started_at is not None, "进入 writing 必须成对写 started_at（与 nightly/_prepare 一致）"
        assert job_is_stuck("writing", job.started_at, datetime.now(timezone.utc)) is False, "刚开跑不该被判卡死"
        assert any("chapter_done" in c for c in chunks)
        assert job.status == "done"

    def test_batch_registers_active_job_while_generating(self, monkeypatch):
        p, job, db, chunks, seen = self._run_one_chapter(monkeypatch)
        assert seen == ["assemble:True"], f"这一章生成期间必须登记在 ACTIVE_JOBS：{seen}"
        assert job.id not in factory_mod.ACTIVE_JOBS, "章末必须注销"

    def test_batch_stream_emits_keepalive_during_upstream_silence(self, monkeypatch):
        """FIX-5：batch 的 SSE 流过去一帧心跳都没有——上游思考期间浏览器侧长时间
        安静，会再现同类「0 字节/被切断」。现在与 _stream_openai 共用同一套骨架。"""
        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "0.2")
        p, job, db, chunks, seen = self._run_one_chapter(monkeypatch, hold=1.0)
        text = "".join(chunks)
        assert text.count(": keepalive") >= 2, "静默期必须持续发心跳把连接撑住"
        assert "chapter_done" in text, "心跳不能把正常流程带崩"


# ---------------------------------------------------------------- FIX-3: 响应体阶段自持会话


class TestPrepareOwnsItsSession:
    """R4 把准备搬进响应体之后，「用哪个会话」就从风格问题变成版本风险（FIX-3）。

    _prepare/_on_error 过去捕获请求级 db（Depends(get_db)）。当前 FastAPI 版本把
    yield 依赖的退出码放在 `await response(...)` **之后**跑，所以侥幸能用；但
    requirements 只写 fastapi>=0.115 不锁版本，而 0.106 正是把这条当破坏性变更动过。
    一旦上游行为回摆，响应体阶段拿到的就是已关闭的会话——表现为「流一切正常，
    状态永远不落库」。现在的修法：自持 SessionLocal（照 _save_assistant_reply 的既有
    写法），闭包只捕 id。
    """

    def test_prepare_and_on_error_do_not_touch_the_request_session(self, monkeypatch):
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        factory_sessions = []

        def spy_factory():
            cm = make_session_factory(db)()
            factory_sessions.append(cm)
            return cm

        monkeypatch.setattr(factory_mod, "SessionLocal", spy_factory)
        patch_upstream(monkeypatch, sse_handler(pieces=("正文",)))

        asyncio.run(_collect_response(factory_mod.generate_chapter(1, 7, None, fake_user(), db)))
        assert factory_sessions, "_prepare 必须自己开一个会话，而不是蹭请求级 db"
        assert db.commits >= 1, "准备阶段那次 commit 必须发生在自持会话上"
        assert job.status == "writing"

    def test_real_sqlite_receives_writing_state_over_asgi(self, monkeypatch):
        """真 ASGI + 真 tmp sqlite（不是 FakeDB）：状态必须真的落到数据库里。

        这是 FakeDB 体系唯一测不到的一层——替身永远「提交成功」。
        """
        from fastapi import FastAPI
        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.deps import get_current_user
        from app.models import AIConfig, AiChapterJob, AiProject, Chapter, Novel, User

        asyncio.run(init_db())
        stamp = int(time.time() * 1000)

        async def seed():
            async with SessionLocal() as s:
                u = User(username=f"sse{stamp}", password_hash="x", role="user")
                s.add(u)
                await s.flush()
                cfg = AIConfig(
                    user_id=u.id, name="真库配置", base_url="https://demo.invalid",
                    api_key="sk-test", model="m-real", is_default=True,
                )
                nv = Novel(user_id=u.id, title="真库之书")
                s.add_all([cfg, nv])
                await s.flush()
                proj = AiProject(
                    user_id=u.id, novel_id=nv.id, status="writing", chapter_llm=None,
                    target_chapter_words=2000, context_recent_chapters=1, author_intent="", current_focus="",
                    seed_prompt="少年得剑，入山问仙。",
                )
                s.add(proj)
                await s.flush()
                ch = Chapter(novel_id=nv.id, title="第一章", content="", sort_order=0, status="draft")
                s.add(ch)
                await s.flush()
                jb = AiChapterJob(project_id=proj.id, chapter_id=ch.id, status="pending", outline="主角入局")
                s.add(jb)
                await s.commit()
                return u.id, proj.id, jb.id

        user_id, project_id, job_id = asyncio.run(seed())

        async def _fast_assemble(p, novel, chapter, job, db):  # 真组装会打璇玑，这里只替这一环
            return "已组装的上下文：主角入局。"

        monkeypatch.setattr(factory_mod, "_assemble_context", _fast_assemble)
        patch_upstream(monkeypatch, sse_handler(pieces=("风起了。",)))

        probe = FastAPI()
        probe.include_router(factory_mod.router)
        probe.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, username="sse", role="user")

        async def call():
            lines = []
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=probe), base_url="http://probe"
            ) as client:
                async with client.stream(
                    "POST", f"/api/ai-factory/projects/{project_id}/jobs/{job_id}/generate", json={"instruction": ""}
                ) as resp:
                    assert resp.status_code == 200, await resp.aread()
                    assert resp.headers["x-accel-buffering"] == "no"
                    async for raw in resp.aiter_lines():
                        if raw.strip():
                            lines.append(raw)
            return lines

        lines = asyncio.run(call())
        text = "\n".join(lines)
        assert "风起了" in text, f"流里必须有正文：{text[:200]}"

        async def read_back():
            async with SessionLocal() as s:
                row = (await s.execute(select(AiChapterJob).where(AiChapterJob.id == job_id))).scalar_one()
                return row.status, row.started_at, row.attempt, row.last_error

        status, started_at, attempt, last_error = asyncio.run(read_back())
        assert status == "writing", f"准备阶段写的状态必须真落库，实际={status}"
        assert started_at is not None, "started_at 必须与 writing 成对落库"
        assert attempt == 1
        assert last_error == ""

    def test_real_sqlite_records_failure_when_assembly_breaks(self, monkeypatch):
        """组装失败那条路径也要真的落到数据库，不能只改替身对象。"""
        from fastapi import FastAPI
        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.deps import get_current_user
        from app.models import AIConfig, AiChapterJob, AiProject, Chapter, Novel, User

        asyncio.run(init_db())
        stamp = int(time.time() * 1000)

        async def seed():
            async with SessionLocal() as s:
                u = User(username=f"ssef{stamp}", password_hash="x", role="user")
                s.add(u)
                await s.flush()
                nv = Novel(user_id=u.id, title="真库之书二")
                cfg = AIConfig(user_id=u.id, name="真库配置", base_url="https://demo.invalid", api_key="sk", model="m", is_default=True)
                s.add_all([nv, cfg])
                await s.flush()
                proj = AiProject(
                    user_id=u.id, novel_id=nv.id, status="writing", author_intent="", current_focus="",
                    seed_prompt="少年得剑，入山问仙。",
                )
                s.add(proj)
                await s.flush()
                ch = Chapter(novel_id=nv.id, title="第一章", content="", sort_order=0)
                s.add(ch)
                await s.flush()
                jb = AiChapterJob(project_id=proj.id, chapter_id=ch.id, status="pending", outline="梗概")
                s.add(jb)
                await s.commit()
                return u.id, proj.id, jb.id

        user_id, project_id, job_id = asyncio.run(seed())

        from fastapi import HTTPException

        async def broken_assemble(p, novel, chapter, job, db):
            raise HTTPException(503, "璇玑知识库不可达")

        monkeypatch.setattr(factory_mod, "_assemble_context", broken_assemble)
        patch_upstream(monkeypatch, sse_handler())

        probe = FastAPI()
        probe.include_router(factory_mod.router)
        probe.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id, username="sse", role="user")

        async def call():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=probe), base_url="http://probe") as client:
                r = await client.post(f"/api/ai-factory/projects/{project_id}/jobs/{job_id}/generate", json={"instruction": ""})
                return r.text

        body = asyncio.run(call())
        assert '"error"' in body, body[:200]

        async def read_back():
            async with SessionLocal() as s:
                row = (await s.execute(select(AiChapterJob).where(AiChapterJob.id == job_id))).scalar_one()
                return row.status, row.last_error, row.last_error_code

        status, last_error, code = asyncio.run(read_back())
        assert status == "failed", "失败必须真落库（否则界面一直转圈在「生成中」）"
        assert "璇玑知识库不可达" in (last_error or "")


# ---------------------------------------------------------------- FIX-4 / FIX-5：细则与心跳复用


def _func_owner(tree: ast.AST, lineno: int) -> str:
    """这行代码落在哪个（可能嵌套的）函数里 —— 取最内层。"""
    best = None
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.lineno <= lineno <= fn.end_lineno:
            if best is None or fn.lineno >= best.lineno:
                best = fn
    return best.name if best else "<module>"


def _expr_signature(node) -> str:
    """把 timeout 实参读成人话：openai_timeout(stream_read_seconds()) / Timeout(20.0) / 30.0。"""
    if node is None:
        return "None"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "?")
        args = ", ".join(_expr_signature(a) for a in node.args)
        return f"{name}({args})"
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return type(node).__name__


def _async_client_timeout_sites(module) -> list[tuple[str, int, str]]:
    """模块里每个 httpx.AsyncClient(...) 调用：（所在函数名, 行号, timeout 实参签名）。

    为什么用 AST 而不是源码正则（FIX-4.7）：字符串匹配会被换行、改名、格式化重构
    误伤或漏放——上一轮就有正则命中 docstring 里反面教材的先例。
    """
    tree = ast.parse(inspect.getsource(module))
    sites: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "AsyncClient":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        sites.append((_func_owner(tree, node.lineno), node.lineno, _expr_signature(kw.get("timeout"))))
    return sites


LLM_SITES = ("ai", "ai_factory", "batch", "relations", "library")
#: 交互式探活白名单：这些地方要的是「快速失败并把原因摊到界面上」，加长只会让
#: 设置页一直转圈（本轮刻意不动，见 app/llm_timeouts 模块说明）。
PROBE_ALLOWLIST = {
    ("ai", "list_config_models"),
    ("ai", "test_connection"),
    ("ai", "list_models"),
    ("ai_extras", "cover_prompt"),
}


class TestTimeoutContractIsAstBased:
    """防回退契约（FIX-4.7 改写版）：用 AST 看清每个调用点，而不是匹配源码字符串。"""

    def test_llm_sites_use_the_central_resolvers(self):
        expect = {
            ("ai", "pump"): "openai_timeout(stream_read_seconds())",
            ("ai_factory", "_chat_text"): "openai_timeout(json_seconds())",
            # 批量连跑的上游等待也进了 pump（与 _stream_openai 同一套心跳骨架）
            ("batch", "pump"): "openai_timeout(stream_read_seconds())",
            ("relations", "extract_relations"): "openai_timeout(aux_seconds())",
            ("library", "organize_item"): "openai_timeout(aux_seconds())",
        }
        actual: dict[tuple[str, str], str] = {}
        for mod_name in LLM_SITES:
            module = __import__(f"app.routers.{mod_name}", fromlist=["*"])
            for fn, _lineno, sig in _async_client_timeout_sites(module):
                actual[(mod_name, fn)] = sig
        for key, sig in expect.items():
            assert actual.get(key) == sig, f"{key} 的超时实参是 {actual.get(key)!r}，应为 {sig!r}"

    def test_no_unaccounted_literal_timeout_in_llm_routers(self):
        """LLM 调用点只许走 llm_timeouts；字面量超时只能出现在探活白名单里，且必须短。"""
        offenders: list[tuple] = []
        probes: list[tuple] = []
        for mod_name in LLM_SITES + ("ai_extras", "ai_assist", "skills"):
            module = __import__(f"app.routers.{mod_name}", fromlist=["*"])
            for fn, lineno, sig in _async_client_timeout_sites(module):
                if sig.startswith("openai_timeout"):
                    # openai_timeout 的实参也必须是解析器，不许塞回字面量
                    inner = sig[len("openai_timeout(") : -1].split(",")[0].strip()
                    if inner not in {"stream_read_seconds()", "json_seconds()", "aux_seconds()"}:
                        offenders.append((mod_name, fn, lineno, sig))
                    continue
                if (mod_name, fn) in PROBE_ALLOWLIST:
                    probes.append((mod_name, fn, sig))
                    continue
                offenders.append((mod_name, fn, lineno, sig))
        assert not offenders, f"这些调用点没走 llm_timeouts（或该进探活白名单并写明理由）：{offenders}"
        assert len(probes) == len(PROBE_ALLOWLIST), f"探活白名单与实际不符：{probes}"

    def test_probe_timeouts_stay_short(self):
        """探活的字面量短超时是**有意的**，被顺手改成 900s 就是回归。"""
        for mod_name, fn in PROBE_ALLOWLIST:
            module = __import__(f"app.routers.{mod_name}", fromlist=["*"])
            sigs = [sig for f, _l, sig in _async_client_timeout_sites(module) if f == fn]
            assert sigs, f"{mod_name}.{fn} 找不到 AsyncClient 调用（改名了？同步更新白名单）"
            for sig in sigs:
                digits = re.findall(r"\d+(?:\.\d+)?", sig)
                assert digits, sig
                assert max(float(d) for d in digits) <= 60, f"{mod_name}.{fn} 探活超时被放宽了：{sig}"


class TestKeepaliveResolutionIsExplicit:
    """FIX-4.1：显式覆盖用 None 作哨兵（过去「与默认相同即视为未覆盖」是玄学）。"""

    def test_none_sentinel_follows_env(self, monkeypatch):
        from app import llm_timeouts

        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "2.5")
        assert llm_timeouts.keepalive_seconds(None) == 2.5
        assert llm_timeouts.keepalive_seconds() == 2.5

    def test_explicit_override_beats_env(self, monkeypatch):
        from app import llm_timeouts

        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "2.5")
        assert llm_timeouts.keepalive_seconds(0.15) == 0.15, "显式传值必须生效（测试与临时调参的入口）"

    def test_module_default_is_no_override(self):
        assert ai_mod.KEEPALIVE_SECONDS is None, "ai.KEEPALIVE_SECONDS 默认应是「不覆盖」，让 env 说话"

    def test_generous_env_warns_instead_of_silently_breaking(self, monkeypatch, caplog):
        """BEIDOU_KEEPALIVE_SECONDS=20 会静默踩回 Envoy 15s idle：接受操作者的选择，但必须警告。"""
        import logging

        from app import llm_timeouts

        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "20")
        with caplog.at_level(logging.WARNING, logger="beidou.llm_timeouts"):
            value = llm_timeouts.keepalive_seconds()
        assert value == 20.0
        assert any("keepalive" in r.message.lower() or "心跳" in r.message for r in caplog.records), (
            "超过网关空闲窗口的取值必须留痕"
        )


class TestTrackerContractIsStrict:
    """FIX-4.2：包装器只接受迭代器；传响应对象要明确报错，而不是悄悄自引用。"""

    def test_passing_a_response_object_raises_typeerror(self):
        async def inner_gen():
            yield "x"

        resp = ai_mod.sse_streaming(inner_gen())
        with pytest.raises(TypeError) as ei:
            factory_mod._track_active_stream(resp, 9201)
        assert "body_iterator" in str(ei.value), "错误信息要直接告诉调用方该传什么"
        assert 9201 not in factory_mod.ACTIVE_JOBS


class TestFailureKindNoConfig:
    """FIX-4.3：「没配 AI 接口」是配置问题，不该落 unknown（与诊断直达入口自相矛盾）。"""

    def test_missing_config_classifies_as_no_config(self):
        from app.failure_kinds import KINDS, classify_failure

        for text in (
            "上下文准备失败：请先在「账号设置」中配置 AI 接口",
            "还没有配置任何 AI 接口",
            "没有可用的模型配置",
            "AI 接口未配置",
        ):
            info = classify_failure(text)
            assert info.code == "no_config", f"{text} → {info.code}"
            assert info.hint, "分类必须给出可操作建议"
        assert "no_config" in KINDS

    def test_real_errors_keep_their_own_kind(self):
        from app.failure_kinds import classify_failure

        assert classify_failure("AI 接口返回 429: quota").code == "quota"
        assert classify_failure("无法连接 AI 接口: ConnectError").code == "network"


class TestStreamEventDetails:
    """FIX-4.4 / 4.5 / 4.6：日志不重复、connected 不撒谎、空列表与缺配置分开说。"""

    def test_connected_event_omits_model_until_config_is_known(self, monkeypatch):
        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db, assemble_delay=0.2)
        patch_upstream(monkeypatch, sse_handler(pieces=("正文",)))

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return await collect(resp.body_iterator)

        events = events_of(asyncio.run(run()))
        assert events[0] == {"stage": "connected"}, f"connected 不该带一个假的 model 字段：{events[0]}"
        stage_models = [e for e in events if e.get("stage") == "model"]
        assert stage_models and stage_models[0]["model"] == make_ai_config().model, "配置就位后应补发 model 事件"

    def test_direct_path_does_not_repeat_the_model_frame(self, monkeypatch):
        """对话入口（config 已在手）：connected 里带 model，就不再补发第二帧 model。

        补发只为逐章生成那条「connected 时还不知道用哪个模型」的路径服务；
        无脑补发会让前端收到两帧语义相同的事件，属于自己制造的噪音。
        """
        patch_upstream(monkeypatch, sse_handler(pieces=("你好呀",)))

        async def run():
            resp = await ai_mod._stream_openai(make_ai_config(), [{"role": "user", "content": "hi"}])
            return await collect(resp.body_iterator)

        events = events_of(asyncio.run(run()))
        assert events[0] == {"stage": "connected", "model": make_ai_config().model}
        assert [e for e in events if e.get("stage") == "model"] == [], "不该重复补发"

    def test_zero_output_logs_exactly_once(self, monkeypatch, caplog):
        import logging

        p, job, db = make_bundle()
        install_guards(monkeypatch, p, db=db)
        patch_upstream(monkeypatch, empty_handler())
        with caplog.at_level(logging.WARNING, logger="beidou.ai"):
            asyncio.run(_collect_response(factory_mod.generate_chapter(1, 7, None, fake_user(), db)))
        hits = [r for r in caplog.records if "零输出" in r.message or "流式生成失败" in r.message]
        assert len(hits) == 1, f"同一条失败打了 {len(hits)} 遍：{[r.message for r in hits]}"

    def test_empty_message_list_gets_its_own_reason(self):
        """prepare 成功但返回 []：不能说成「没有拿到可用的模型配置」（FIX-4.6）。"""

        async def empty_prepare():
            return make_ai_config(), []

        async def run():
            resp = await ai_mod._stream_openai(None, None, prepare=empty_prepare)
            return await collect(resp.body_iterator)

        events = events_of(asyncio.run(run()))
        err = next(e["error"] for e in events if "error" in e)
        assert "配置" not in err, f"配置其实是齐的，别把成功说成失败：{err}"
        assert "消息" in err or "为空" in err


class TestNightlyRegistersActiveJob:
    """FIX-2.3：夜跑单章最坏是 900s + 900s + 关系同步，设计上就会超旧的 15 分钟阈值。"""

    def test_generate_one_registers_and_unregisters(self, monkeypatch):
        from app import nightly as nightly_mod

        p, job, db = make_bundle()
        from app.models import User as UserModel

        db.rows[UserModel] = SimpleNamespace(id=1, username="u", role="user")
        seen: list[bool] = []

        async def _pick_config(user, session, route_field):
            return make_ai_config()

        async def _assemble_context(project, novel, chapter, jb, session):
            return "上下文"

        async def _chat_text(config, system, prompt, **kwargs):
            if not seen:
                seen.append(job.id in factory_mod.ACTIVE_JOBS)
            return "正文" * 300

        async def _update_state_files(*args, **kwargs):
            return False

        async def _sync_relations(*args, **kwargs):
            return None

        async def _maybe_split(*args, **kwargs):
            return 1

        monkeypatch.setattr(nightly_mod, "_pick_config", _pick_config)
        monkeypatch.setattr(nightly_mod, "_assemble_context", _assemble_context)
        monkeypatch.setattr(nightly_mod, "_chat_text", _chat_text)
        monkeypatch.setattr(nightly_mod, "_update_state_files", _update_state_files)
        monkeypatch.setattr(nightly_mod, "_sync_relations_from_chapter", _sync_relations)
        monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", _maybe_split)
        monkeypatch.setattr(nightly_mod, "detect", lambda text: {"score": 95, "issues": []})
        monkeypatch.setattr(nightly_mod, "_record_usage", lambda *a, **k: None)

        result = asyncio.run(nightly_mod._generate_one(p, db.rows[Novel], job, db.rows[Chapter], 1, db))
        assert result["ok"] is True, result
        assert seen == [True], "生成期间必须登记在 ACTIVE_JOBS"
        assert job.id not in factory_mod.ACTIVE_JOBS, "跑完必须注销"
        assert job.started_at is not None
