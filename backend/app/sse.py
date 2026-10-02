"""SSE 响应的统一出口（R3）：media_type + 抗代理缓冲头。

**为什么必须有这层**：`proxy_buffering on`（nginx 类边缘的默认）会把上游已经写出的
字节攒在代理缓冲区里再一起下发。于是服务端明明在正常吐 `connected` 和心跳，客户端
却仍然看到「HTTP 200 + text/event-stream + 0 字节」——和真凶（首字太慢被掐连接）
长得一模一样，排查时被当成同一件事。两个头就能关掉它：

- ``X-Accel-Buffering: no``  nginx 认这个头，对单个响应关掉缓冲；
- ``Cache-Control: no-cache``  防中间层缓存/合并这段流（也顺手避免复用旧响应）。

所有 SSE 端点请走 ``sse_streaming(...)``，不要裸写
``StreamingResponse(gen(), media_type="text/event-stream")``——
routers/ai.py / batch.py / ai_import.py 三处都由本模块构造，测试会盯住这点。
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable

from fastapi.responses import StreamingResponse

__all__ = [
    "SSE_HEADERS",
    "SSE_MEDIA_TYPE",
    "paced_await",
    "paced_upstream",
    "sse_headers",
    "sse_streaming",
]

SSE_MEDIA_TYPE = "text/event-stream"

#: 抗缓冲 / 抗缓存头（值不要改：nginx 只认 "no"，大小写敏感的客户端按字面匹配）
SSE_HEADERS: dict[str, str] = {
    "X-Accel-Buffering": "no",
    "Cache-Control": "no-cache",
}


def sse_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """SSE 响应头；extra 用于个别端点追加自己的头（同 key 以 extra 为准）。"""
    headers = dict(SSE_HEADERS)
    if extra:
        headers.update({k: v for k, v in extra.items() if v})
    return headers


async def paced_upstream(pump, *, keepalive: float | None = None):
    """「上游行搬运 + 静默心跳」的公共骨架（routers/ai.py 与 routers/batch.py 共用）。

    为什么要有它：等上游吐第一个 token 期间，连接可以几十秒什么都不过线，网关/代理
    会把它当空闲连接掐掉，客户端于是收到「HTTP 200 + text/event-stream + 0 字节」。
    解法只有一种——安静的时候也往线上扔字节。这套「队列 + wait_for(keepalive)」原本
    长在 _stream_openai 里，批量连跑没复用，于是同一个坑摔了两次（batch 一帧心跳都没有）。

    ``pump(queue)``：协程。把上游每行 ``await queue.put(("line", line))``；出错
    ``put(("fatal", 文案))``；``finally`` 里 ``put(("eof", None))``。

    本生成器按序产出 ``("idle", None)``（静默超过 keepalive 秒，调用方据此发
    ``: keepalive``）/ ``("line", str)`` / ``("fatal", str)`` / ``("eof", None)``，
    并在 fatal/eof 之后自然收尾。无论正常结束、出错还是上层 aclose（客户端断开），
    ``finally`` 都会取消 pump —— 否则那个正等上游、读超时最长 15 分钟的任务会继续
    占着连接与上游配额。
    """
    import asyncio

    from . import llm_timeouts

    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(pump(queue))
    gap = llm_timeouts.keepalive_seconds(keepalive)
    try:
        while True:
            try:
                kind, item = await asyncio.wait_for(queue.get(), timeout=gap)
            except asyncio.TimeoutError:
                yield ("idle", None)
                continue
            yield (kind, item)
            if kind in ("eof", "fatal"):
                return
    finally:
        task.cancel()
        # 只 cancel 不等待，等于把「上游响应是否真的关掉了」推给不确定的调度时机：
        # 客户端断开后那条正等着 15 分钟读超时的请求还会占着连接与配额。
        # asyncio.wait 不会把子任务的取消抛回本帧（本帧自己被打断时它会正常再抛）。
        await asyncio.wait({task}, timeout=2.0)


async def paced_await(awaitable, *, keepalive: float | None = None):
    """「长 await + 静默心跳」的可复用件（FIX-6B，与 paced_upstream 同风格）。

    场景：SSE 流里除了等上游，还有**本地发起的多段长调用**——batch 每章正文生成
    之后的 AI 味改写 / 状态文件更新 / 关系同步 / 摘要都是完整的一次 LLM 往返
    （超时口径最长 15 分钟）。裸 await 期间整条流一个字节都不发，Envoy/CF 会在
    15 秒～100 秒级把这条「看似空闲」的连接掐掉，整批停在当前章（天演探针实测
    该段静默 3.01 秒起步，放宽超时后最坏 15 分钟）。

    用法（结果通过最后一帧交回，异常原样外抛）::

        box: list = []
        async for kind, item in paced_await(some_long_coro()):
            if kind == "idle":
                yield ": keepalive\\n\\n"
            else:            # ("done", result)
                box.append(item)
        result = box[0]

    契约：
    - 每静默 gap 秒产出 ``("idle", None)``（gap 走 llm_timeouts.keepalive_seconds）；
    - awaitable 完成后产出 ``("done", result)`` 并收尾；
    - awaitable 的异常在迭代处**原样外抛**（绝不包装、绝不吞）——怎么处置失败
      （落 failed / 用原稿）仍归调用方决定，本函数只负责「等待期间线上有字节」；
    - 上层中途弃用本生成器（客户端断开）时，finally 取消底层任务，不留一个
      还在等 15 分钟超时的孤儿协程。
    """
    import asyncio

    from . import llm_timeouts

    gap = llm_timeouts.keepalive_seconds(keepalive)
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=gap)
            if done:
                # 任务若带异常，task.result() 在这里把原异常抛回调用方（async for 处）。
                yield ("done", task.result())
                return
            yield ("idle", None)
    finally:
        if not task.done():
            task.cancel()
            # 同 paced_upstream：只 cancel 不等待会把「是否真的停了」推给调度运气。
            await asyncio.wait({task}, timeout=2.0)


def sse_streaming(
    content: AsyncIterable[str] | Iterable[str],
    *,
    headers: dict[str, str] | None = None,
    status_code: int = 200,
) -> StreamingResponse:
    """构造一个带抗缓冲头的 SSE 响应。"""
    return StreamingResponse(
        content,
        media_type=SSE_MEDIA_TYPE,
        headers=sse_headers(headers),
        status_code=status_code,
    )
