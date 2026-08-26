"""integrations.py AList 诊断的轻量路由级测试：直呼路由函数 + MockTransport 注入。

不启动 FastAPI app、不触碰数据库与真实网络；_get_config 与 AlistClient 构造均打桩。
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import app.routers.integrations as integ


def envelope(code: int = 200, message: str = "success", data: object = None) -> dict:
    return {"code": code, "message": message, "data": data}


USER = SimpleNamespace(id=1)


def _happy_handler_factory(root: str = "/beidou"):
    """全链路正常的假 AList：根目录存在，子目录可建，可写可删。
    root 参数化以便同一假服务既可测 /beidou 也可测 /（账户基本路径）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        path = request.url.path
        body = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}
        if path == "/api/auth/login":
            return httpx.Response(200, json=envelope(data={"token": "tok"}))
        if path == "/api/fs/list":
            return httpx.Response(200, json=envelope(data={"content": [], "total": 0}))
        if path == "/api/fs/get":
            if body.get("path") in (root, f"{root}/backup", f"{root}/uploads", f"{root}/covers"):
                return httpx.Response(200, json=envelope(data={"is_dir": True}))
            return httpx.Response(200, json=envelope(500, "failed get object: object not found"))
        return httpx.Response(200, json=envelope())  # mkdir/put/remove 均成功

    return handler


def _login_rejected_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/login":
        return httpx.Response(200, json=envelope(403, "wrong username or password"))
    raise AssertionError("登录被拒后不应再发业务请求")


def _network_down_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_config(user, db):
        return SimpleNamespace(
            alist_url="https://alist.example.com", alist_username="u", alist_password="p", alist_root="/beidou"
        )

    monkeypatch.setattr(integ, "_get_config", fake_get_config)


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    orig_init = integ.AlistClient.__init__

    def patched(self, base_url, username, password, transport=None):
        orig_init(self, base_url, username, password, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(integ.AlistClient, "__init__", patched)


# ---------- 三步诊断 ----------


@pytest.mark.asyncio
async def test_alist_success_message(monkeypatch: pytest.MonkeyPatch):
    _stub_config(monkeypatch)
    _patch_client(monkeypatch, _happy_handler_factory())

    result = await integ.test_alist(user=USER, db=None)

    assert result["ok"] is True
    assert result["message"] == "连接成功，/beidou（backup、uploads、covers）已就绪，读写均正常"


@pytest.mark.asyncio
async def test_alist_network_failure_mentions_connectivity(monkeypatch: pytest.MonkeyPatch):
    """M4：status==0（请求未达业务层）时报连接问题，而不是误导用户去核对密码。"""
    _stub_config(monkeypatch)
    _patch_client(monkeypatch, _network_down_handler)

    with pytest.raises(HTTPException) as excinfo:
        await integ.test_alist(user=USER, db=None)

    detail = excinfo.value.detail
    assert "无法连接 AList" in detail and "地址与网络" in detail
    assert "核对用户名和密码" not in detail


@pytest.mark.asyncio
async def test_alist_login_rejected_mentions_credentials(monkeypatch: pytest.MonkeyPatch):
    """仅业务层登录拒绝才提示核对用户名和密码。"""
    _stub_config(monkeypatch)
    _patch_client(monkeypatch, _login_rejected_handler)

    with pytest.raises(HTTPException) as excinfo:
        await integ.test_alist(user=USER, db=None)

    detail = excinfo.value.detail
    assert "AList 拒绝了登录" in detail and "wrong username or password" in detail


@pytest.mark.asyncio
async def test_alist_with_account_base_path_root_succeeds(monkeypatch: pytest.MonkeyPatch):
    """AList 后台把账户基本路径设到某本地存储时，前端把 alist_root 留空（保存后归一为 /）也应能成功。"""
    _stub_config(monkeypatch)
    _patch_client(monkeypatch, _happy_handler_factory(root="/"))

    # 覆写 stub：alist_root 已是归一化后的值 "/"
    async def fake_get_config(user, db):
        return SimpleNamespace(
            alist_url="https://alist.example.com", alist_username="u", alist_password="p", alist_root="/"
        )

    monkeypatch.setattr(integ, "_get_config", fake_get_config)

    result = await integ.test_alist(user=USER, db=None)

    assert result["ok"] is True
    assert result["message"] == "连接成功，/（backup、uploads、covers）已就绪，读写均正常"
