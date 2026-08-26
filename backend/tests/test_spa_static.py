"""SPA 静态回退路径穿越回归测试。

验证目标：``app.main`` 的 SPA 回退路由在遇到 ``../``、URL 编码（如
``%2e%2e%2f``）等形式时，一律回退 index.html，绝不泄露静态目录之外的
任意文件内容；同时保证正常静态资源与前端路由回退行为不受影响。
"""

import os
from pathlib import Path
from typing import Tuple

import httpx
import pytest

from app.main import app

# 本模块所有用例皆为异步（pytest-asyncio strict 模式需显式标记）
pytestmark = [pytest.mark.asyncio]

# conftest 已在导入前把 STATIC_DIR 指向临时假静态站点，这里直接取期望值
_STATIC_ROOT = Path(os.environ["STATIC_DIR"])
EXPECTED_INDEX = (_STATIC_ROOT / "index.html").read_text(encoding="utf-8")
EXPECTED_APP_JS = (_STATIC_ROOT / "assets" / "app.js").read_text(encoding="utf-8")
# 静态目录之外的同级秘密文件内容（穿越攻击的靶标，任何响应中都不允许出现）
SECRET_BODY = (_STATIC_ROOT.parent / "secret.txt").read_text(encoding="utf-8")


def _make_client() -> httpx.AsyncClient:
    """构造直连 ASGI 应用的测试客户端（ASGITransport 不触发 lifespan，跳过建库）。"""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def _call_raw(raw_target: bytes) -> Tuple[int, bytes]:
    """以未归一化的原始请求路径直接驱动 ASGI 应用。

    httpx 客户端发送前会把 ``/../x`` 归一化为 ``/x``（RFC 3986 去点段），
    要验证服务端对字面量点段路径的防御，必须手工构造 scope 绕过客户端归一化。
    """
    messages: list[dict] = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": raw_target.decode("latin-1"),
        "raw_path": raw_target,
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test")],
        "client": ("testclient", 50000),
        "server": ("test", 80),
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in messages if m["type"] == "http.response.body"
    )
    return status, body


async def test_assets_js_served() -> None:
    """a. 静态资源正常返回：GET /assets/app.js 返回 200 且内容正确。"""
    async with _make_client() as client:
        resp = await client.get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.text == EXPECTED_APP_JS


async def test_percent_encoded_traversal_falls_back() -> None:
    """b1. GET /%2e%2e%2fsecret.txt 必须回退 index.html，绝不出现 secret 内容。"""
    async with _make_client() as client:
        resp = await client.get("/%2e%2e%2fsecret.txt")
    assert SECRET_BODY not in resp.text
    assert resp.text == EXPECTED_INDEX


async def test_double_percent_encoded_traversal_falls_back() -> None:
    """b1+. 多级编码穿越 /%2e%2e%2f%2e%2e%2fsecret.txt 同样只允许回退 index.html。"""
    async with _make_client() as client:
        resp = await client.get("/%2e%2e%2f%2e%2e%2fsecret.txt")
    assert SECRET_BODY not in resp.text
    assert resp.text == EXPECTED_INDEX


async def test_twice_encoded_traversal_falls_back() -> None:
    """b1++. 二次编码穿越 /%252e%252e%252fsecret.txt 只允许回退 index.html。

    ``%25`` 是 ``%`` 的编码：Starlette 解码一层后路径参数为字面量
    ``%2e%2e%2fsecret.txt``，不会再次解码成 ``../``，因此目标文件不存在，
    必须回退 index.html 且绝不出现 secret 内容（防双重解码类漏洞回归）。
    """
    async with _make_client() as client:
        resp = await client.get("/%252e%252e%252fsecret.txt")
    assert SECRET_BODY not in resp.text
    assert resp.text == EXPECTED_INDEX


async def test_literal_dotdot_traversal_falls_back() -> None:
    """b2. 字面量 GET /../secret.txt（未归一化原始路径）同样只允许回退 index.html。"""
    status, body = await _call_raw(b"/../secret.txt")
    text = body.decode("utf-8")
    assert SECRET_BODY not in text
    assert text == EXPECTED_INDEX
    assert status == 200


async def test_unknown_route_falls_back() -> None:
    """c. 前端路由回退：GET /some/unknown/route 返回 index.html。"""
    async with _make_client() as client:
        resp = await client.get("/some/unknown/route")
    assert resp.status_code == 200
    assert resp.text == EXPECTED_INDEX


async def test_root_serves_index() -> None:
    """d. 根路径：GET / 返回 index.html。"""
    async with _make_client() as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.text == EXPECTED_INDEX
