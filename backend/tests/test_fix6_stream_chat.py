"""FIX-6A / FIX-6B 回归测试：_chat_text 流式累积（治 CF 524）+ batch 后阶段心跳。

线上证据（实时库 /data/beidou/beidou.db，job 5/6/7，2026-10-01）的 last_error 原文：
    重试后仍失败：HTTPException: 502: AI 接口返回 524: <!DOCTYPE html>...
- 「重试后仍失败：」= nightly._generate_one_with_retry 的二次失败包装前缀；
- 「HTTPException: 502」= ai_factory._chat_text 对非 200 的统一包装；
- 524 = Cloudflare 边缘在 100 秒内没等到源站响应（tianshu.xianrealme.com 实测在
  CF 后面：server: cloudflare / cf-ray / 104.21.89.82）。

根因（FIX-6A）：_chat_text 写死 stream:False，整章 8000 token 一次性返回，思考型
模型整包常超 100 秒 → CF 边缘把连接掐成 524。与用户选哪个模型无关；上一轮把本
服务客户端超时放宽到 900 秒对它无效（掐断方是边缘，不是本服务）。
解法：向上游请求 stream:True 并累积 delta，让边缘持续看到字节，调用方签名与行为不变。

FIX-6B（上轮评审 MAJOR-1）：batch_run 每章正文生成之后的多段裸 await（AI 味改写/
状态文件/关系同步/摘要）期间 SSE 一帧不发，最坏静默 15 分钟——Envoy/CF 在
15s~100s 级就掐，整批停在当前章。用 app/sse.paced_await 在等待期间发 ": keepalive"。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_fix6_stream_chat.py -q
（零真实网络：上游一律 httpx.MockTransport 打桩；单文件 <30 秒）
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app import sse as sse_mod
from app.failure_kinds import classify_failure
from app.models import AIConfig, AiChapterJob, AiProject, Chapter, Novel
from app.routers import ai_factory as factory_mod
from app.routers import batch as batch_mod


# ---------------------------------------------------------------- 通用替身


def make_config() -> AIConfig:
    return AIConfig(
        id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
    )


SSE_CT = {"content-type": "text/event-stream"}
JSON_CT = {"content-type": "application/json"}


def sse_body(*deltas, usage=None, tail_after_done=None) -> bytes:
    """把若干 delta 拼成 OpenAI 兼容的 SSE 响应体；可选 usage 尾帧与 [DONE] 之后的杂帧。"""
    lines = ["data: " + json.dumps({"choices": [{"delta": d}]}, ensure_ascii=False) for d in deltas]
    if usage is not None:
        lines.append("data: " + json.dumps({"choices": [], "usage": usage}))
    lines.append("data: [DONE]")
    if tail_after_done is not None:
        lines.append("data: " + json.dumps({"choices": [{"delta": {"content": tail_after_done}}]}))
    return ("\n\n".join(lines) + "\n\n").encode()


def install_stub(monkeypatch, responses) -> list[dict]:
    """把 httpx.AsyncClient 指到 MockTransport 桩（零网络），返回捕获到的请求 payload 列表。

    responses：[(status, headers, body)] 队列——只剩一个时无限复用它；
    元素也可以是 Exception（从 handler 里抛出，模拟连接层故障）。
    """
    requests: list[dict] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        status, headers, body = item
        return httpx.Response(status, headers=headers, content=body)

    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return requests


# ---------------------------------------------------------------- FIX-6A：_chat_text 流式累积


class TestChatTextStreamsUpstream:
    """_chat_text 必须向上游要流并累积 delta（治 CF 524），对调用方完全不变。"""

    def test_request_asks_for_stream_and_usage(self, monkeypatch):
        requests = install_stub(monkeypatch, [(200, SSE_CT, sse_body({"content": "好"}))])
        text = asyncio.run(factory_mod._chat_text(make_config(), "sys", "user"))
        assert text == "好"
        payload = requests[0]
        assert payload["stream"] is True, "必须向上游请求流式——CF 524 的根源就是整包一次性返回"
        assert payload.get("stream_options") == {"include_usage": True}, "要拿精确 usage 回填成本账本"
        assert payload["max_tokens"] == 4000, "默认 max_tokens 保持原签名行为"
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]

    def test_delta_content_accumulates_in_order(self, monkeypatch):
        install_stub(monkeypatch, [(200, SSE_CT, sse_body({"content": "夜"}, {"content": "风"}, {"content": "起了"}))])
        assert asyncio.run(factory_mod._chat_text(make_config(), "s", "u")) == "夜风起了"

    def test_reasoning_content_is_discarded(self, monkeypatch):
        """delta.reasoning_content 是思考段，必须丢弃，一个字都不许混进正文。"""
        install_stub(
            monkeypatch,
            [
                (
                    200,
                    SSE_CT,
                    sse_body(
                        {"reasoning_content": "先想想"},
                        {"content": "正"},
                        {"reasoning_content": "再想想"},
                        {"content": "文"},
                    ),
                )
            ],
        )
        assert asyncio.run(factory_mod._chat_text(make_config(), "s", "u")) == "正文"

    def test_done_marker_stops_consumption(self, monkeypatch):
        install_stub(monkeypatch, [(200, SSE_CT, sse_body({"content": "正文"}, tail_after_done="DONE后杂帧"))])
        assert asyncio.run(factory_mod._chat_text(make_config(), "s", "u")) == "正文"

    def test_stream_usage_flows_into_sink(self, monkeypatch):
        install_stub(
            monkeypatch,
            [(200, SSE_CT, sse_body({"content": "好"}, usage={"prompt_tokens": 11, "completion_tokens": 7}))],
        )
        sink: dict = {}
        text = asyncio.run(factory_mod._chat_text(make_config(), "s", "u", usage_sink=sink))
        assert text == "好"
        assert sink == {"prompt": 11, "completion": 7}, "include_usage 拿到的用量必须回填 usage_sink"

    def test_non_200_keeps_message_shape_and_no_retry(self, monkeypatch):
        requests = install_stub(monkeypatch, [(500, JSON_CT, b"upstream exploded")])
        with pytest.raises(factory_mod.HTTPException) as ei:
            asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert ei.value.status_code == 502
        assert str(ei.value.detail).startswith("AI 接口返回 500: upstream exploded"), "非 200 文案形状必须原样保留"
        assert len(requests) == 1, "5xx 不属于 stream_options 回退场景，不该重试"

    def test_4xx_from_stream_options_retries_once_without_it(self, monkeypatch):
        """部分网关不认 stream_options（4xx）：去掉它重试一次，仍流式。"""
        requests = install_stub(
            monkeypatch,
            [
                (400, JSON_CT, b'{"error": "Unknown field: stream_options"}'),
                (200, SSE_CT, sse_body({"content": "回退成功"})),
            ],
        )
        text = asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert text == "回退成功"
        assert len(requests) == 2
        assert requests[0].get("stream_options") == {"include_usage": True}
        assert "stream_options" not in requests[1], "回退请求必须去掉 stream_options"
        assert requests[1]["stream"] is True, "回退请求仍是流式"

    def test_4xx_twice_raises_with_original_shape(self, monkeypatch):
        requests = install_stub(monkeypatch, [(400, JSON_CT, b'{"error":"bad request"}'), (400, JSON_CT, b'{"error":"bad request"}')])
        with pytest.raises(factory_mod.HTTPException) as ei:
            asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert ei.value.status_code == 502
        assert "AI 接口返回 400" in str(ei.value.detail)
        assert len(requests) == 2, "只回退一次，不做无限重试"

    def test_non_sse_content_type_falls_back_to_json(self, monkeypatch):
        """必须的兜底：有些网关忽略 stream 参数直接回 JSON——原 JSON 解析要还能用。"""
        body = json.dumps(
            {
                "choices": [{"message": {"content": "JSON路径的文本"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }
        ).encode()
        requests = install_stub(monkeypatch, [(200, JSON_CT, body)])
        sink: dict = {}
        text = asyncio.run(factory_mod._chat_text(make_config(), "s", "u", usage_sink=sink))
        assert text == "JSON路径的文本"
        assert sink == {"prompt": 3, "completion": 4}, "JSON 兜底路径的 usage 也要回填"
        assert requests[0]["stream"] is True

    def test_json_fallback_without_content_type_header(self, monkeypatch):
        """裸 bytes 响应连 content-type 都没有（钉住 test_sse_hardening 既有场景）也要走 JSON 兜底。"""
        body = json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
        install_stub(monkeypatch, [(200, {}, body)])
        assert asyncio.run(factory_mod._chat_text(make_config(), "s", "u")) == '{"ok":true}'

    def test_empty_stream_raises_empty_output_and_classifies(self, monkeypatch):
        """推理模型只回 reasoning_content（或断流零字节）→ 报「空内容」而不是静默。"""
        install_stub(monkeypatch, [(200, SSE_CT, sse_body({"reasoning_content": "只想不写"}))])
        with pytest.raises(factory_mod.HTTPException) as ei:
            asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert ei.value.status_code == 502
        detail = str(ei.value.detail)
        assert "空内容" in detail and "流式" in detail, "流式路径的空内容文案要如实标注通道"
        assert classify_failure(detail).code == "empty_output"

    def test_json_fallback_empty_content_keeps_nonstream_wording(self, monkeypatch):
        body = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
        install_stub(monkeypatch, [(200, JSON_CT, body)])
        with pytest.raises(factory_mod.HTTPException) as ei:
            asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert "非流式" in str(ei.value.detail), "JSON 兜底语义上仍是非流式响应，通道标注不变"
        assert classify_failure(str(ei.value.detail)).code == "empty_output"

    def test_json_fallback_garbage_is_502_not_500(self, monkeypatch):
        install_stub(monkeypatch, [(200, JSON_CT, b"this is not json")])
        with pytest.raises(factory_mod.HTTPException) as ei:
            asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert ei.value.status_code == 502
        assert "AI 接口响应格式异常" in str(ei.value.detail)

    def test_midstream_disconnect_never_returns_partial_text(self, monkeypatch):
        """断流半截：宁可报错也绝不把半章正文当成品返回（半截正文落库=毒章节）。

        注意桩必须**不带 [DONE]**——真实的断流到不了终止帧；sse_body 助手会追加
        [DONE]，用它造桩会让 _chat_text 在断流前正常 break，测了个寂寞。
        """

        async def body():
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": "半截"}}]}, ensure_ascii=False) + "\n\n").encode()
            raise httpx.RemoteProtocolError("peer closed connection without sending complete message body")

        install_stub(monkeypatch, [(200, SSE_CT, body())])
        with pytest.raises(factory_mod.HTTPException) as ei:
            asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert ei.value.status_code == 502
        assert classify_failure(str(ei.value.detail)).code == "empty_output"

    def test_timeout_still_follows_json_seconds(self, monkeypatch):
        """超时口径不变：openai_timeout(json_seconds())——本修不许顺手加裸超时。"""
        from app import llm_timeouts

        real = llm_timeouts.openai_timeout
        seen: list = []
        monkeypatch.setattr(llm_timeouts, "openai_timeout", lambda s, **kw: (seen.append(s), real(s, **kw))[1])
        install_stub(monkeypatch, [(200, SSE_CT, sse_body({"content": "好"}))])
        asyncio.run(factory_mod._chat_text(make_config(), "s", "u"))
        assert seen == [llm_timeouts.json_seconds()]


# ---------------------------------------------------------------- FIX-6B：paced_await + batch 后阶段


class TestPacedAwaitHelper:
    """app/sse.paced_await：长 await 期间产 idle 心跳、结果交回、异常原样外抛。"""

    def test_helper_exists_in_sse_module(self):
        assert hasattr(sse_mod, "paced_await"), "sse 模块缺 paced_await 可复用件（FIX-6B）"

    def test_yields_idle_frames_then_result(self):
        async def run():
            async def slow():
                await asyncio.sleep(0.3)
                return 42

            frames = []
            async for kind, item in sse_mod.paced_await(slow(), keepalive=0.1):
                frames.append((kind, item))
            return frames

        frames = asyncio.run(run())
        idles = [f for f in frames if f[0] == "idle"]
        assert len(idles) >= 2, f"0.3s 任务按 0.1s 心跳至少 2 帧 idle：{frames}"
        assert frames[-1] == ("done", 42), "完成后必须把结果交回调用方"

    def test_exception_is_reraised_unchanged(self):
        async def run():
            async def boom():
                await asyncio.sleep(0.05)
                raise ValueError("后阶段炸了")

            out = []
            async for kind, item in sse_mod.paced_await(boom(), keepalive=5):
                out.append(kind)
            return out

        with pytest.raises(ValueError, match="后阶段炸了"):
            asyncio.run(run()), "异常必须原样外抛，绝不许被心跳包装吞掉"

    def test_abandoned_generator_cancels_the_task(self):
        async def run():
            cancelled: list = []

            async def slow():
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise
                return 1

            gen = sse_mod.paced_await(slow(), keepalive=0.05)
            await gen.__anext__()  # 至少吃一帧 idle
            await gen.aclose()
            await asyncio.sleep(0.01)
            return cancelled

        assert asyncio.run(run()) == [True], "弃用后必须取消底层任务，不能留一个等超时的孤儿协程"


# ---------------- batch 集成替身（自包含，不依赖 test_sse_hardening）----------------


class _FakeDB:
    def __init__(self, rows: dict):
        self.rows = rows
        self.commits = 0

    async def get(self, model, pk):
        return self.rows.get(model)

    async def commit(self):
        self.commits += 1


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _BatchDB(_FakeDB):
    """batch_run 用的替身会话：execute 依次吐 jobs / chapters / volumes。"""

    def __init__(self, rows, results):
        super().__init__(rows)
        self._results = list(results)

    async def execute(self, stmt):
        return _Rows(self._results.pop(0) if self._results else [])


def make_batch_bundle():
    p = SimpleNamespace(
        id=1, user_id=1, status="writing", novel_id=11, chapter_llm=None, summary_llm=None,
        target_chapter_words=2000, context_recent_chapters=2, author_intent="", current_focus="",
        tokens_prompt=0, tokens_completion=0,
    )
    job = SimpleNamespace(
        id=7, project_id=1, chapter_id=21, status="pending", attempt=0, started_at=None,
        last_error="", last_error_code="", outline="主角入局", summary="", actual_words=0, finished_at=None,
    )
    chapter = SimpleNamespace(
        id=21, novel_id=11, volume_id=None, title="第一章 开局", sort_order=0,
        content="<p>旧文</p>", word_count=0, status="draft",
    )
    novel = SimpleNamespace(id=11, title="测试之书")
    return p, job, chapter, novel


async def collect_response(coro):
    resp = await coro
    return [chunk async for chunk in resp.body_iterator]


def install_batch(monkeypatch, *, p, post_state, chat_text=None):
    """把 batch_run 跑通一章的外部依赖全部替身化；post_state 顶替状态文件更新。"""
    from app import nightly as nightly_mod

    async def _get_project(project_id, user, db):
        return p

    async def _pick_config(user, db, route_field):
        return make_config()

    async def _assemble_context(*a):
        return "已组装的上下文：主角入局。"

    async def _update_state_files(*a, **k):
        return await post_state()

    async def _sync_relations(*a, **k):
        return 0

    async def _maybe_split(*a, **k):
        return 1

    async def _chat(*a, **k):
        if chat_text is not None:
            return await chat_text()
        return "本章摘要：主角入局。"

    monkeypatch.setattr(batch_mod, "_get_project", _get_project)
    monkeypatch.setattr(batch_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(batch_mod, "_update_state_files", _update_state_files)
    monkeypatch.setattr(batch_mod, "_chat_text", _chat)
    monkeypatch.setattr(batch_mod, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(batch_mod, "detect", lambda text: {"score": 95, "issues": []})
    monkeypatch.setattr(factory_mod, "_assemble_context", _assemble_context)
    monkeypatch.setattr(factory_mod, "_sync_relations_from_chapter", _sync_relations)
    monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", _maybe_split)

    def handler(request):
        async def body():
            for _ in range(40):
                yield ("data: " + json.dumps({"choices": [{"delta": {"content": "风起了。"}}]}) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, content=body())

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw.setdefault("transport", httpx.MockTransport(handler))
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


class TestBatchPostStageKeepalive:
    """FIX-6B：正文生成之后的多段长 await 期间，batch 流必须继续发 ': keepalive'。"""

    def _run(self, monkeypatch, post_state, chat_text=None):
        monkeypatch.setenv("BEIDOU_KEEPALIVE_SECONDS", "0.2")
        p, job, chapter, novel = make_batch_bundle()
        install_batch(monkeypatch, p=p, post_state=post_state, chat_text=chat_text)
        db = _BatchDB(
            {Novel: novel, AiProject: p, Chapter: chapter, AiChapterJob: job},
            ([job], [chapter], []),
        )
        user = SimpleNamespace(id=1, role="user")
        chunks = asyncio.run(collect_response(batch_mod.batch_run(1, 5, user, db)))
        return job, chunks

    def test_post_stage_silence_emits_keepalive(self, monkeypatch):
        """假后阶段睡 3 秒（天演探针实测 3.01s 静默）：期间必须 >= 2 帧 ': keepalive'。"""

        async def post_state():
            await asyncio.sleep(3.0)
            return True

        job, chunks = self._run(monkeypatch, post_state)
        text = "".join(chunks)
        assert text.count(": keepalive") >= 2, "正文生成之后的长 await 期间也必须有帧，否则 Envoy/CF 照掐"
        assert "chapter_done" in text, "心跳不能把正常收尾带崩"
        assert job.status == "done"

    def test_post_stage_exception_marks_chapter_failed(self, monkeypatch):
        """后阶段异常不许被心跳包装吞掉：该章必须落 failed 并给出原因。"""

        async def post_state():
            await asyncio.sleep(0.5)
            raise RuntimeError("状态文件炸了")

        job, chunks = self._run(monkeypatch, post_state)
        text = "".join(chunks)
        assert job.status == "failed", "后阶段异常必须让该章落 failed，而不是静默断流/卡死在 writing"
        assert "状态文件炸了" in (job.last_error or ""), "失败原因必须落 last_error 供诊断"
        assert '"event": "error"' in text, "错误要作为 SSE 事件发给前端"
        assert "chapter_done" not in text, "失败的章不该再发 chapter_done"
        assert text.count(": keepalive") >= 2, "异常前的静默期同样要有帧"
