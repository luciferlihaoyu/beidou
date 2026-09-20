"""流式转发（_stream_openai）的端到端测试：起一个本地 stub 端点，真发真收。

为什么要这组测试：用户报「点生成，过一会什么都没有」。真实原因是本函数过去在
上游吐出第一个 token 之前**一个字节都不发**，等待期间的空闲连接会被网关/代理掐掉，
客户端收到一个「干净结束的空响应」——既没有正文也没有错误事件。
这里用本地 stub 把四种情形钉死（正常 / 上游零输出 / 首字很慢 / 上游报错 / 连不上），
以后谁再改动流式实现，都会立刻暴露。

运行：PYTHONPATH=. pytest tests/test_stream_openai.py -q
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.models import AIConfig
from app.routers import ai as ai_mod


class _Handler(BaseHTTPRequestHandler):
    mode = "normal"  # normal | empty | slow | http_error | cut
    delay = 0.0
    protocol_version = "HTTP/1.1"  # 用 chunked 才能模拟「分块传输中硬断」

    def log_message(self, *_a):  # 静音
        pass

    def do_POST(self):
        cls = type(self)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

        if cls.mode == "http_error":
            body = b'{"error":{"message":"boom"}}'
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(payload: str):
            self.wfile.write(f"{len(payload):X}\r\n".encode() + payload.encode() + b"\r\n")
            self.wfile.flush()

        def end_stream():
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

        if cls.mode == "empty":
            # 上游 200、规规矩矩结束，却一个增量都没有（推理模型只回 reasoning_content 时就是这样）
            send("data: [DONE]\n\n")
            end_stream()
            return

        if cls.mode == "slow":
            # 模拟长思考：首字前长时间静默——过去这段时间一个字节都不发给客户端
            time.sleep(cls.delay)

        if cls.mode == "cut":
            # 吐了半个字就硬断（不发结束块）：模拟网关/代理把连接掐掉
            send("data: " + json.dumps({"choices": [{"delta": {"content": "你"}}]}) + "\n\n")
            self.close_connection = True
            self.connection.close()
            return

        for piece in ("你", "好"):
            send("data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}) + "\n\n")
        send("data: [DONE]\n\n")
        end_stream()


class _Stub:
    """本地 stub 端点（随机端口，用完即关）。"""

    def __init__(self, mode="normal", delay=0.0):
        handler = type("H", (_Handler,), {"mode": mode, "delay": delay})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _config(base_url: str, model: str = "test-model") -> AIConfig:
    return AIConfig(id=1, user_id=1, name="测试配置", base_url=base_url, api_key="sk-test", model=model)


def _collect(config: AIConfig) -> list[str]:
    """跑完整条流，返回原始 SSE 文本块列表。"""

    async def run():
        resp = await ai_mod._stream_openai(config, [{"role": "user", "content": "写一章"}])
        return [chunk async for chunk in resp.body_iterator]

    return asyncio.run(run())


def _events(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data:"):
                out.append(json.loads(line[5:].strip()))
    return out


class TestStreamContract:
    def test_normal_stream_sends_connected_first_then_content_then_done(self):
        stub = _Stub("normal")
        try:
            chunks = _collect(_config(stub.base_url))
        finally:
            stub.close()
        events = _events(chunks)
        assert events[0]["stage"] == "connected", "必须先发 connected：让字节立刻流动"
        assert events[0]["model"] == "test-model"
        assert [e.get("content") for e in events if "content" in e] == ["你", "好"]
        done = [e for e in events if e.get("done")]
        assert done and done[0]["chars"] == 2
        assert not [e for e in events if "error" in e]

    def test_empty_upstream_reports_error_instead_of_silent_done(self):
        """本次真凶之一：上游 200 零增量。以前只发 done → 前端无错无字。"""
        stub = _Stub("empty")
        try:
            chunks = _collect(_config(stub.base_url))
        finally:
            stub.close()
        events = _events(chunks)
        errors = [e["error"] for e in events if "error" in e]
        assert errors, "零输出必须报错，不能静默结束"
        assert "空内容" in errors[0]
        assert "test-model" in errors[0], "必须点名模型"
        assert stub.base_url in errors[0], "必须点名端点"
        assert not [e for e in events if e.get("done")], "零输出不该报 done"

    def test_slow_first_token_keeps_connection_warm(self):
        """首字很慢时必须持续发心跳——否则等待期的空闲连接会被网关掐断。"""
        original = ai_mod.KEEPALIVE_SECONDS
        ai_mod.KEEPALIVE_SECONDS = 0.15
        stub = _Stub("slow", delay=0.8)
        try:
            chunks = _collect(_config(stub.base_url))
        finally:
            stub.close()
            ai_mod.KEEPALIVE_SECONDS = original

        text = "".join(chunks)
        assert ": keepalive" in text, "慢首字期间必须有心跳"
        # 第一条心跳必须出现在首个正文之前（这才是「撑住等待期」的意义）
        assert text.index(": keepalive") < text.index('"content"'), "心跳要早于首字"
        events = _events(chunks)
        # 心跳是 SSE 注释行，不会被解析成事件
        assert [e.get("content") for e in events if "content" in e] == ["你", "好"]
        assert [e for e in events if e.get("done")]

    def test_upstream_http_error_becomes_error_event(self):
        stub = _Stub("http_error")
        try:
            chunks = _collect(_config(stub.base_url))
        finally:
            stub.close()
        errors = [e["error"] for e in _events(chunks) if "error" in e]
        assert errors and "500" in errors[0]

    def test_unreachable_endpoint_becomes_error_event(self):
        """连不上时也要发 error 事件，而不是让流静静结束。"""
        stub = _Stub("normal")
        base = stub.base_url
        stub.close()  # 关掉，端口随即不可达
        chunks = _collect(_config(base))
        errors = [e["error"] for e in _events(chunks) if "error" in e]
        assert errors, "连不上必须报错"
        assert "无法连接" in errors[0] or "生成中断" in errors[0]

    def test_half_written_then_connection_cut_reports_error(self):
        """吐了一半就被掐断：已收到的内容要留住，同时必须报错——不能把半章当完整章。"""
        stub = _Stub("cut")
        try:
            chunks = _collect(_config(stub.base_url))
        finally:
            stub.close()
        events = _events(chunks)
        assert [e.get("content") for e in events if "content" in e] == ["你"], "半截内容要留住"
        assert [e for e in events if "error" in e], "被掐断必须报错，否则半章会被当成完整章定稿"
        assert not [e for e in events if e.get("done")]
