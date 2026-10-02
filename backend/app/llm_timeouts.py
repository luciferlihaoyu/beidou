"""LLM 调用的超时与 SSE 心跳集中配置（R1）。

**为什么要收成一处**：正文生成是 SSE 长流，一次「一章」在 upstream 的等待可以到
几分钟（思考型模型首字尤其慢）。过去超时散落在各路由里（120s / 180s / 300s / 60s），
改一处忘一处，而且全都短于真实生成时间——超时一到，客户端看到的就是
「HTTP 200 + text/event-stream + 0 字节」，前端只能报「连接在模型返回第一个字之前
被切断了」。心跳同理：原来的 10s 心跳一旦抖动就踩上 Envoy 默认 15s 的 idle 阈值。

默认值按「每个环节至少放宽 5–15 分钟」的要求设定，全部可用环境变量覆盖：

| 环境变量 | 含义 | 默认 |
| --- | --- | --- |
| BEIDOU_LLM_STREAM_READ_SECONDS | 流式上游读超时（逐章生成 / 对话 / 批量连跑） | 900（15 分钟） |
| BEIDOU_LLM_JSON_SECONDS        | 非流式 JSON 调用（立项 / 设定 / 大纲 / 审校）     | 900（15 分钟） |
| BEIDOU_LLM_AUX_SECONDS         | 辅助 LLM（关系抽取 / 素材归类）                   | 600（10 分钟） |
| BEIDOU_KEEPALIVE_SECONDS       | SSE 心跳间隔                                      | 5    |

非法值（空 / 非数字 / ≤0 / 超过 24 小时 / inf / nan）一律回落默认，绝不让一个
写错的 env 把生成打成秒断。

**刻意不进本模块的**：模型清单（GET /v1/models）与连通性测试这类交互式探活——
它们要的是「快速失败并把原因摊到界面上」，加长只会让设置页面一直转圈。
见 routers/ai.py 里保持 20s / 30s 的那几处。
"""

from __future__ import annotations

import logging
import math
import os

import httpx

logger = logging.getLogger("beidou.llm_timeouts")

__all__ = [
    "STREAM_READ_SECONDS",
    "JSON_SECONDS",
    "AUX_SECONDS",
    "KEEPALIVE_SECONDS",
    "stream_read_seconds",
    "json_seconds",
    "aux_seconds",
    "keepalive_seconds",
    "stuck_minutes",
    "openai_timeout",
]

# 合理上界：24 小时。超过它多半是有人写错了单位（毫秒当秒），宁可回落默认。
_MAX_SECONDS = 86400.0

#: 心跳超过这个秒数就基本失去意义：Envoy 路由默认 15s 空闲即断（nginx 常配 60s）。
KEEPALIVE_WARN_CEILING_SECONDS = 10.0

#: 「卡死」阈值的天数上限（防止有人把分钟数配成秒数，1 分钟就把在跑的任务全解锁）
_MAX_STUCK_MINUTES = 24 * 60


def _env_float(name: str, default: float) -> float:
    """读取一个「秒」环境变量；脏值/越界一律回落 default（绝不抛错、绝不让 0 生效）。"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0 or value > _MAX_SECONDS:
        return default
    return value


def _positive(value, default: float) -> float:
    """把调用方传来的数值兜成可用秒数（防止 None / 字符串 / 0 / 负数）。"""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or v <= 0 or v > _MAX_SECONDS:
        return default
    return v


# ---------- 模块导入时定下的默认值（供展示 / 断言 / 各调用点兜底）----------

STREAM_READ_SECONDS = _env_float("BEIDOU_LLM_STREAM_READ_SECONDS", 900.0)
JSON_SECONDS = _env_float("BEIDOU_LLM_JSON_SECONDS", 900.0)
AUX_SECONDS = _env_float("BEIDOU_LLM_AUX_SECONDS", 600.0)
KEEPALIVE_SECONDS = _env_float("BEIDOU_KEEPALIVE_SECONDS", 5.0)

DEFAULT_CONNECT_SECONDS = 15.0


# ---------- 运行期解析（每次调用都重新读 env，方便临时调参与测试）----------


def stream_read_seconds() -> float:
    """流式上游读超时（秒）。"""
    return _env_float("BEIDOU_LLM_STREAM_READ_SECONDS", STREAM_READ_SECONDS)


def json_seconds() -> float:
    """非流式 JSON 调用超时（秒）。"""
    return _env_float("BEIDOU_LLM_JSON_SECONDS", JSON_SECONDS)


def aux_seconds() -> float:
    """辅助 LLM 调用超时（秒）。"""
    return _env_float("BEIDOU_LLM_AUX_SECONDS", AUX_SECONDS)


def keepalive_seconds(override: float | None = None) -> float:
    """SSE 心跳间隔（秒）。

    优先级（显式，不搞「与默认值相同就算没改」那种玄学）：
      1. ``override`` 不为 None —— 调用方**显式**指定的覆盖值（routers/ai.py 的
         KEEPALIVE_SECONDS 就是这个入口：默认 None，被赋值才生效）；
      2. 环境变量 ``BEIDOU_KEEPALIVE_SECONDS``；
      3. 本模块默认 ``KEEPALIVE_SECONDS``（5s）。
    """
    if override is not None:
        return _positive(override, KEEPALIVE_SECONDS)
    value = _env_float("BEIDOU_KEEPALIVE_SECONDS", KEEPALIVE_SECONDS)
    if value > KEEPALIVE_WARN_CEILING_SECONDS:
        # 心跳一旦比网关空闲窗口（Envoy 路由默认 15s）还大，等于没有心跳：
        # 尊重操作者的取值，但必须在日志里留痕，别让它静默退化成 0 字节事故。
        logger.warning(
            "BEIDOU_KEEPALIVE_SECONDS=%s 超过 %ss 的网关空闲窗口，"
            "等待上游期间可能仍会被代理掐断连接（建议 ≤5s）",
            value,
            KEEPALIVE_WARN_CEILING_SECONDS,
        )
    return value


def stuck_minutes() -> int:
    """「生成中断」判定阈值（分钟）——必须跟随超时口径，不能硬编码 15 分钟。

    单章最坏耗时 = 流式读超时 + 一次非流式收尾（JSON 口径），再留 5 分钟余量：
    低于这个时长的 writing 任务不该被自动解锁，否则就是把正在跑的活儿当成尸体。
    可用 ``BEIDOU_STUCK_MINUTES`` 显式覆盖。
    """
    override = _env_float("BEIDOU_STUCK_MINUTES", 0.0)
    if override > 0:
        return max(1, min(int(override), _MAX_STUCK_MINUTES))
    total = stream_read_seconds() + json_seconds()
    return max(1, min(math.ceil(total / 60.0) + 5, _MAX_STUCK_MINUTES))


def openai_timeout(read_seconds: float, connect: float = DEFAULT_CONNECT_SECONDS) -> httpx.Timeout:
    """构造上游 httpx 超时：read/write/pool 同值（长等），connect 保持短。

    connect 保持短是有意的——连不上要立刻失败并报「哪个端点不通」，
    把连接超时拖长只会让用户对着转圈的界面干等。
    """
    read = _positive(read_seconds, STREAM_READ_SECONDS)
    return httpx.Timeout(read, connect=_positive(connect, DEFAULT_CONNECT_SECONDS))
