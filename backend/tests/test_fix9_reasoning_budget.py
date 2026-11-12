"""FIX-9：强思维链模型耗尽 token 预算时自动翻倍重试一次。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_fix9_reasoning_budget.py -q
所有上游响应均由 httpx.MockTransport 提供，零真实网络。
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from app import nightly as nightly_mod
from app.failure_kinds import classify_failure
from app.models import AIConfig, AiChapterJob, AiProject, Chapter, Novel, User
from app.routers import ai as ai_mod
from app.routers import ai_factory as factory_mod


SSE_HEADERS = {"content-type": "text/event-stream"}


def make_config() -> AIConfig:
    return AIConfig(
        id=1,
        user_id=1,
        name="推理配置",
        base_url="https://upstream.invalid",
        api_key="sk-test",
        model="MiniMax-M3.1-Flash",
    )


def delta_chunk(delta: dict, finish_reason: str | None = None) -> dict:
    return {"choices": [{"delta": delta, "finish_reason": finish_reason}]}


def usage_chunk(prompt_tokens: int, completion_tokens: int) -> dict:
    return {
        "choices": [],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def sse_body(*chunks: dict) -> bytes:
    lines = ["data: " + json.dumps(chunk, ensure_ascii=False) for chunk in chunks]
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


def install_stub(monkeypatch, responses: list) -> list[dict]:
    """按请求次序返回假响应，并捕获每次完整重新发起的 payload。"""
    queue = list(responses)
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode()))
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, tuple):
            status, headers, body = item
            return httpx.Response(status, headers=headers, content=body)
        return httpx.Response(200, headers=SSE_HEADERS, content=item)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return requests


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class FakeDB:
    """只实现本轮调用链实际用到的接口，绝不触真实数据库。"""

    def __init__(self, rows: dict):
        self.rows = rows
        self.commits = 0

    async def get(self, model, pk):
        return self.rows.get(model)

    async def execute(self, statement):
        return _EmptyResult()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    def add(self, obj):
        return None


def session_factory(db: FakeDB):
    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    return _Session


def make_pipeline_bundle(*, target_words=0):
    p = SimpleNamespace(
        id=1,
        user_id=1,
        status="writing",
        novel_id=11,
        chapter_llm=None,
        summary_llm=None,
        target_chapter_words=target_words,
        author_intent="",
        current_focus="",
        tokens_prompt=0,
        tokens_completion=0,
    )
    job = SimpleNamespace(
        id=7,
        project_id=1,
        chapter_id=21,
        status="pending",
        attempt=0,
        started_at=None,
        finished_at=None,
        last_error="",
        last_error_code="",
        outline="主角入局",
        summary="",
        actual_words=0,
        draft_text=None,
        draft_updated_at=None,
    )
    chapter = SimpleNamespace(
        id=21,
        novel_id=11,
        volume_id=None,
        title="第一章",
        sort_order=0,
        content="<p>旧文</p>",
        word_count=0,
        status="draft",
    )
    novel = SimpleNamespace(id=11, title="测试之书", author="甲", genre="玄幻", description="")
    user = SimpleNamespace(id=1, username="u", role="user")
    db = FakeDB({AiProject: p, AiChapterJob: job, Chapter: chapter, Novel: novel, User: user})
    return p, job, chapter, novel, db


def install_ui_chapter_guards(monkeypatch, p, db):
    async def get_project(project_id, user, session):
        return p

    async def pick_config(user, session, route_field):
        return make_config()

    async def assemble_context(project, novel, chapter, job, session):
        return "章节上下文"

    async def save_draft(job_id, text):
        return None

    monkeypatch.setattr(factory_mod, "_get_project", get_project)
    monkeypatch.setattr(factory_mod, "_pick_config", pick_config)
    monkeypatch.setattr(factory_mod, "_assemble_context", assemble_context)
    monkeypatch.setattr(factory_mod, "_save_job_draft", save_draft)
    monkeypatch.setattr(factory_mod, "SessionLocal", session_factory(db))


def install_nightly_guards(monkeypatch, *, score=95):
    async def pick_config(user, session, route_field):
        return make_config()

    async def assemble_context(project, novel, chapter, job, session):
        return "夜跑上下文"

    async def no_result(*args, **kwargs):
        return None

    async def no_split(*args, **kwargs):
        return 1

    monkeypatch.setattr(nightly_mod, "_pick_config", pick_config)
    monkeypatch.setattr(nightly_mod, "_assemble_context", assemble_context)
    monkeypatch.setattr(nightly_mod, "_update_state_files", no_result)
    monkeypatch.setattr(nightly_mod, "_sync_relations_from_chapter", no_result)
    monkeypatch.setattr(nightly_mod, "_maybe_split_chapter", no_split)
    monkeypatch.setattr(nightly_mod, "detect", lambda text: {"score": score, "issues": []})


def test_chat_text_retries_once_when_finish_length_is_all_reasoning(monkeypatch):
    """finish=length + 正文为空 + reasoning 非空：8000 → 16000 整请求重试。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(
                delta_chunk({"reasoning_content": "想" * 120}, "length"),
                usage_chunk(11, 8000),
            ),
            sse_body(
                delta_chunk({"reasoning_content": "再想"}),
                delta_chunk({"content": "正文"}, "stop"),
                usage_chunk(13, 25),
            ),
        ],
    )
    sink: dict = {}

    text = asyncio.run(factory_mod._chat_text(make_config(), "sys", "写一章", max_tokens=8000, usage_sink=sink))

    assert text == "正文"
    assert [request["max_tokens"] for request in requests] == [8000, 16000]
    assert all(request["stream"] is True for request in requests)
    assert sink == {"prompt": 24, "completion": 8025}, "两轮 usage 都要各自入账，不得漏记或重复"


def test_chat_text_usage_fallback_retries_when_gateway_omits_finish_reason(monkeypatch):
    """网关吞 finish_reason 时：空正文 + reasoning + completion_tokens 到预算，仍须重试。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(
                delta_chunk({"reasoning_content": "推" * 80}),
                usage_chunk(7, 4000),
            ),
            sse_body(delta_chunk({"content": "兜底重试成功"}, "stop"), usage_chunk(9, 18)),
        ],
    )

    text = asyncio.run(factory_mod._chat_text(make_config(), "sys", "写", max_tokens=4000))

    assert text == "兜底重试成功"
    assert [request["max_tokens"] for request in requests] == [4000, 8000]


def test_chat_text_caps_retry_at_24000_and_logs_both_rounds(monkeypatch, caplog):
    """16000 仍不够时第二轮必须到 24000；日志展示两轮实测而非让用户猜。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "思" * 100}, "length"), usage_chunk(5, 16000)),
            sse_body(
                delta_chunk({"reasoning_content": "考虑"}),
                delta_chunk({"content": "完整正文"}, "stop"),
                usage_chunk(6, 30),
            ),
        ],
    )
    caplog.set_level(logging.INFO, logger="beidou.ai_factory")

    text = asyncio.run(factory_mod._chat_text(make_config(), "sys", "写", max_tokens=16000))

    assert text == "完整正文"
    assert [request["max_tokens"] for request in requests] == [16000, 24000]
    assert "推理配置" in caplog.text and "MiniMax-M3.1-Flash" in caplog.text
    assert "重试预算=24000" in caplog.text
    assert "第一轮 content=0/reasoning=100 字" in caplog.text
    assert "第二轮 content=4/reasoning=2 字" in caplog.text
    assert "重试轮 usage 已另行入账" in caplog.text


def test_chat_text_failed_retry_reports_measured_reasoning_and_actual_budget(monkeypatch, caplog):
    """24000 仍只回推理：错误文案与 warning 都要带两轮实测。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 100}, "length"), usage_chunk(5, 16000)),
            sse_body(delta_chunk({"reasoning_content": "末" * 17000}, "length"), usage_chunk(6, 24000)),
        ],
    )
    sink: dict = {}
    caplog.set_level(logging.WARNING, logger="beidou.ai_factory")

    with pytest.raises(factory_mod.HTTPException) as exc_info:
        asyncio.run(factory_mod._chat_text(make_config(), "sys", "写", max_tokens=16000, usage_sink=sink))

    assert len(requests) == 2, "自适应重试最多一次"
    detail = str(exc_info.value.detail)
    assert exc_info.value.status_code == 502
    assert "模型返回空内容（流式）" in detail
    assert "本次模型思考约 17000 字仍未输出正文（预算 24000 tokens 已耗尽）" in detail
    assert classify_failure(detail).code == "empty_output"
    assert sink == {"prompt": 11, "completion": 40000}
    assert "重试仍失败" in caplog.text
    assert "推理配置" in caplog.text and "MiniMax-M3.1-Flash" in caplog.text
    assert "第一轮预算=16000" in caplog.text and "第二轮预算=24000" in caplog.text
    assert "第一轮 content=0/reasoning=100 字" in caplog.text
    assert "第二轮 content=0/reasoning=17000 字" in caplog.text


def test_chat_text_second_round_stop_with_low_usage_uses_generic_empty_error(monkeypatch):
    """第二轮 finish=stop 且用量远低于预算：不许谎报『预算已耗尽』。"""
    install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 30}, "length"), usage_chunk(3, 16000)),
            sse_body(delta_chunk({"reasoning_content": "空"}, "stop"), usage_chunk(4, 12)),
        ],
    )

    with pytest.raises(factory_mod.HTTPException) as exc_info:
        asyncio.run(factory_mod._chat_text(make_config(), "sys", "写", max_tokens=16000))

    detail = str(exc_info.value.detail)
    assert "模型返回空内容（流式）" in detail
    assert "预算已耗尽" not in detail
    assert "本次模型思考约" not in detail


def test_chat_text_nonempty_length_response_is_returned_without_retry(monkeypatch):
    """FIX-9C：已有 158 字正文时 finish=length 也原样返回，把补足交给 FIX-8。"""
    short_chapter = "短" * 158
    requests = install_stub(
        monkeypatch,
        [
            sse_body(
                delta_chunk({"reasoning_content": "先想"}),
                delta_chunk({"content": short_chapter}, "length"),
                usage_chunk(5, 8000),
            )
        ],
    )

    text = asyncio.run(factory_mod._chat_text(make_config(), "sys", "写", max_tokens=8000))

    assert text == short_chapter
    assert len(requests) == 1, "content 非空不属于 FIX-9，绝不能与 FIX-8 补足双保险打架"


def collect_events(chunks: list[str]) -> list[dict]:
    return [
        json.loads(line[5:].strip())
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data:")
    ]


def test_ui_stream_options_4xx_falls_back_without_changing_budget(monkeypatch):
    """include_usage 兼容性：不认字段的网关去掉它重发，不能误翻预算或发 error。"""
    requests = install_stub(
        monkeypatch,
        [
            (400, {"content-type": "application/json"}, b'{"error":"Unknown field: stream_options"}'),
            sse_body(delta_chunk({"content": "兼容正文"}, "stop")),
        ],
    )

    async def run():
        response = await ai_mod._stream_openai(
            make_config(), [{"role": "user", "content": "写"}], max_tokens=8000
        )
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    assert len(requests) == 2
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in requests[1]
    assert [request["max_tokens"] for request in requests] == [8000, 8000]
    assert [event["content"] for event in events if "content" in event] == ["兼容正文"]
    assert not any("error" in event for event in events)


def test_ui_stream_retries_reasoning_exhaustion_inside_same_sse(monkeypatch):
    """9B：UI 原始流首轮只回推理，第二轮正文帧须直接接进同一条 SSE。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "想" * 60}, "length"), usage_chunk(5, 8000)),
            sse_body(
                delta_chunk({"reasoning_content": "再想"}),
                delta_chunk({"content": "夜"}),
                delta_chunk({"content": "深"}, "stop"),
                usage_chunk(6, 20),
            ),
        ],
    )

    async def run():
        response = await ai_mod._stream_openai(
            make_config(), [{"role": "user", "content": "写一章"}], max_tokens=8000
        )
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    assert [request["max_tokens"] for request in requests] == [8000, 16000]
    assert sum(event.get("stage") == "connected" for event in events) == 1
    assert [event["content"] for event in events if "content" in event] == ["夜", "深"]
    assert [event for event in events if event.get("done")] == [{"done": True, "chars": 2}]
    assert not any("error" in event for event in events)


def test_ui_stream_failed_retry_emits_measured_error_once(monkeypatch):
    """UI 第二轮仍为空：同一条 SSE 发含实测的 error，on_error 收到同文案，无 done。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 20}, "length"), usage_chunk(3, 8000)),
            sse_body(delta_chunk({"reasoning_content": "末" * 900}, "length"), usage_chunk(4, 16000)),
        ],
    )
    errors: list[str] = []

    async def on_error(message: str):
        errors.append(message)

    async def run():
        response = await ai_mod._stream_openai(
            make_config(), [{"role": "user", "content": "写"}], on_error=on_error, max_tokens=8000
        )
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    assert len(requests) == 2
    error_events = [event["error"] for event in events if "error" in event]
    assert len(error_events) == 1
    assert "本次模型思考约 900 字仍未输出正文（预算 16000 tokens 已耗尽）" in error_events[0]
    assert errors == error_events
    assert not any(event.get("done") for event in events)


def test_ui_stream_nonempty_length_response_does_not_retry(monkeypatch):
    """UI 已有正文被 length 截断时也不许自动重试，避免已透传正文重复。"""
    requests = install_stub(
        monkeypatch,
        [sse_body(delta_chunk({"content": "已有正文"}, "length"), usage_chunk(3, 8000))],
    )

    async def run():
        response = await ai_mod._stream_openai(make_config(), [{"role": "user", "content": "写"}])
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    assert len(requests) == 1
    assert [event["content"] for event in events if "content" in event] == ["已有正文"]
    assert [event for event in events if event.get("done")] == [{"done": True, "chars": 4}]


def test_ui_stream_usage_fallback_also_retries(monkeypatch):
    """UI 流同样覆盖网关吞 finish_reason 的 usage 兜底，并主动请求精确 usage。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "推" * 50}), usage_chunk(3, 8000)),
            sse_body(delta_chunk({"content": "正文回来了"}, "stop"), usage_chunk(4, 20)),
        ],
    )

    async def run():
        response = await ai_mod._stream_openai(
            make_config(), [{"role": "user", "content": "写"}], max_tokens=8000
        )
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    assert [request["max_tokens"] for request in requests] == [8000, 16000]
    assert all(request.get("stream_options") == {"include_usage": True} for request in requests)
    assert "".join(event["content"] for event in events if "content" in event) == "正文回来了"
    assert not any("error" in event for event in events)


# ---------------------------------------------------------------------------
# 第二版必修（天演第六次复评 NO-GO 清单）：
#   ① 共享流默认预算必须回到 None（防长文润色被静默截断）
#   ② 章节入口预算序列必须是 [16000, 24000]（16000 仍不够，必须够得到 24000）
#   ③ 「预算已耗尽」文案不得谎报（第二轮非耗尽时用通用文案）
#   ④ 失败必须留日志（两轮预算 + 两轮实测）
# ---------------------------------------------------------------------------


def test_ui_stream_default_has_no_budget_and_no_retry(monkeypatch):
    """默认 max_tokens=None：payload 不带 max_tokens，空正文也不翻倍重试（只发一次请求）。"""
    requests = install_stub(
        monkeypatch,
        [sse_body(delta_chunk({"reasoning_content": "想" * 30}, "length"), usage_chunk(3, 8000))],
    )

    async def run():
        response = await ai_mod._stream_openai(make_config(), [{"role": "user", "content": "写"}])
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    assert len(requests) == 1, "没有预算就没有第二轮可翻倍，绝不能因为 reasoning 就偷偷重发"
    assert "max_tokens" not in requests[0], "None 预算 = 旧行为，请求体不带 max_tokens"
    error_events = [event["error"] for event in events if "error" in event]
    assert len(error_events) == 1, "空正文必须报错，不能静默发 done"
    assert not any(event.get("done") for event in events)


def test_ui_stream_second_round_not_exhausted_uses_generic_error(monkeypatch):
    """第二轮 finish=stop 且用量远低于预算：不许谎报『预算已耗尽』。"""
    install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 30}, "length"), usage_chunk(3, 8000)),
            sse_body(delta_chunk({"reasoning_content": "空"}, "stop"), usage_chunk(4, 12)),
        ],
    )

    async def run():
        response = await ai_mod._stream_openai(
            make_config(), [{"role": "user", "content": "写"}], max_tokens=8000
        )
        return [chunk async for chunk in response.body_iterator]

    events = collect_events(asyncio.run(run()))

    error_events = [event["error"] for event in events if "error" in event]
    assert len(error_events) == 1
    assert "模型返回空内容（流式）" in error_events[0]
    assert "预算已耗尽" not in error_events[0]
    assert "本次模型思考约" not in error_events[0]


def test_ui_stream_failed_retry_logs_both_rounds(monkeypatch, caplog):
    """失败必须留日志：两轮预算 + 两轮实测都能在日志里追到。"""
    install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 20}, "length"), usage_chunk(3, 8000)),
            sse_body(delta_chunk({"reasoning_content": "末" * 900}, "length"), usage_chunk(4, 16000)),
        ],
    )
    caplog.set_level(logging.WARNING, logger="beidou.ai")

    async def run():
        response = await ai_mod._stream_openai(
            make_config(), [{"role": "user", "content": "写"}], max_tokens=8000
        )
        return [chunk async for chunk in response.body_iterator]

    asyncio.run(run())

    assert "重试仍失败" in caplog.text
    assert "第一轮预算=8000" in caplog.text
    assert "第二轮预算=16000" in caplog.text
    assert "第一轮 content=0/reasoning=20 字" in caplog.text
    assert "第二轮 content=0/reasoning=900 字" in caplog.text


def test_quick_action_polish_asks_for_24000_budget(monkeypatch):
    """整章润色必须显式要 24000 预算（输入允许 15000 字、要求返回全文，不能被截断）。"""
    captured: dict = {}

    async def fake_stream_openai(config, messages, **kwargs):
        captured.update(kwargs)
        captured["messages"] = messages
        return SimpleNamespace(body_iterator=iter([]))

    monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai)

    async def get_owned_novel(novel_id, user, db):
        return SimpleNamespace(id=11, title="测试之书")

    async def get_ai_config(user, db, config_id):
        return make_config()

    monkeypatch.setattr(ai_mod, "get_owned_novel", get_owned_novel)
    monkeypatch.setattr(ai_mod, "get_ai_config", get_ai_config)

    chapter = SimpleNamespace(id=21, novel_id=11, title="第一章", content="<p>" + "旧" * 200 + "</p>")

    class _DB:
        async def get(self, model, pk):
            return chapter

    payload = ai_mod.QuickActionIn(novel_id=11, chapter_id=21)
    asyncio.run(ai_mod.quick_action("polish", payload, SimpleNamespace(id=1), _DB()))

    assert captured.get("max_tokens") == 24000, "润色必须给足预算，否则长文会被静默截断"


def test_quick_action_non_polish_has_no_budget(monkeypatch):
    """续写/大纲/审查沿用旧行为：不传预算 = 不限长。"""
    captured: dict = {}

    async def fake_stream_openai(config, messages, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(body_iterator=iter([]))

    monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai)

    async def get_owned_novel(novel_id, user, db):
        return SimpleNamespace(id=11, title="测试之书")

    async def get_ai_config(user, db, config_id):
        return make_config()

    async def novel_context(novel, db, chapter_id):
        return "上下文"

    monkeypatch.setattr(ai_mod, "get_owned_novel", get_owned_novel)
    monkeypatch.setattr(ai_mod, "get_ai_config", get_ai_config)
    monkeypatch.setattr(ai_mod, "_novel_context", novel_context)

    payload = ai_mod.QuickActionIn(novel_id=11)
    asyncio.run(ai_mod.quick_action("continue", payload, SimpleNamespace(id=1), SimpleNamespace()))

    assert captured.get("max_tokens") is None, "非润色动作不该被套上任何上限"


def test_nightly_chapter_budget_sequence_is_16000_then_24000(monkeypatch):
    """夜跑章节入口：首轮 16000、推理耗尽后翻倍到 24000（16000 实测仍不够）。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 100}, "length"), usage_chunk(5, 16000)),
            sse_body(
                delta_chunk({"reasoning_content": "再想"}),
                delta_chunk({"content": "夜跑正文" * 30}, "stop"),
                usage_chunk(6, 30),
            ),
        ],
    )
    p, job, chapter, novel, db = make_pipeline_bundle(target_words=0)
    install_nightly_guards(monkeypatch, score=95)

    result = asyncio.run(nightly_mod._generate_chapter(p, novel, job, chapter, 1, db))

    # 前两轮 = 章节生成的预算序列；第 3 次是定稿摘要（max_tokens=400），不参与断言。
    assert [request["max_tokens"] for request in requests][:2] == [16000, 24000]
    assert result.get("ok") is True


def test_ui_chapter_entry_budget_sequence_is_16000_then_24000(monkeypatch):
    """UI 章节生成入口：首轮 16000、推理耗尽后翻倍到 24000。"""
    requests = install_stub(
        monkeypatch,
        [
            sse_body(delta_chunk({"reasoning_content": "首" * 90}, "length"), usage_chunk(5, 16000)),
            sse_body(
                delta_chunk({"reasoning_content": "再想"}),
                delta_chunk({"content": "界面正文" * 30}, "stop"),
                usage_chunk(6, 30),
            ),
        ],
    )
    p, job, chapter, novel, db = make_pipeline_bundle(target_words=0)
    install_ui_chapter_guards(monkeypatch, p, db)

    async def run():
        response = await factory_mod.generate_chapter(p.id, job.id, None, SimpleNamespace(id=1), db)
        return [chunk async for chunk in response.body_iterator]

    asyncio.run(run())

    assert [request["max_tokens"] for request in requests] == [16000, 24000]
