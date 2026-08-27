"""从 AList 拉备份还原（手动）端点测试：
- GET /api/integrations/alist/backups —— 列出 beidou-*.zip，按 modified_at 倒序
- GET /api/integrations/alist/backups/{filename}/download —— 流式下载 zip，路径防穿越
"""

import io
from types import SimpleNamespace

import httpx
import pytest

import app.routers.integrations as integ_mod


def _stub_config(monkeypatch, *, root="/"):
    async def fake_get_config(user, db):
        return SimpleNamespace(
            alist_url="https://alist.example.com", alist_username="u", alist_password="p", alist_root=root
        )
    monkeypatch.setattr(integ_mod, "_get_config", fake_get_config)


def _patch_alist_list(monkeypatch, items: list[dict]):
    """Mock AlistClient.list_dir 直接返回给定 items。返回 instance 用于断言调用参数。"""
    list_calls: list[str] = []

    class _FakeClient:
        def __init__(self, base_url, username, password, transport=None):
            pass

        async def list_dir(self, path: str):
            list_calls.append(path)
            return list(items)

    instance = _FakeClient("u", "u", "p")
    monkeypatch.setattr(integ_mod, "AlistClient", lambda *a, **kw: instance)
    return type("Calls", (), {"list_calls": list_calls})


def _patch_alist_get_bytes(monkeypatch, payload: bytes):
    """Mock AlistClient.get_bytes 返回给定 bytes。"""
    get_calls: list[str] = []

    class _FakeClient:
        def __init__(self, base_url, username, password, transport=None):
            pass

        async def get_bytes(self, path: str):
            get_calls.append(path)
            return payload

    instance = _FakeClient("u", "u", "p")
    monkeypatch.setattr(integ_mod, "AlistClient", lambda *a, **kw: instance)
    return type("Calls", (), {"get_calls": get_calls})


def _patch_alist_error(monkeypatch, *, list_error: str | None = None, get_error: str | None = None):
    """Mock AlistClient 抛 AlistError。"""
    from app.alist import AlistError

    class _ErrClient:
        def __init__(self, base_url, username, password, transport=None):
            pass

        async def list_dir(self, path: str):
            if list_error:
                raise AlistError(list_error, status=500)
            return []

        async def get_bytes(self, path: str):
            if get_error:
                raise AlistError(get_error, status=404)
            return b""

    instance = _ErrClient("u", "u", "p")
    monkeypatch.setattr(integ_mod, "AlistClient", lambda *a, **kw: instance)


# ---------- 列备份 ----------


@pytest.mark.asyncio
async def test_list_backups_filters_and_sorts_desc(monkeypatch):
    """只列 beidou-*.zip 文件；按 modified_at 倒序。"""
    _stub_config(monkeypatch, root="/")
    _patch_alist_list(monkeypatch, items=[
        {"name": "beidou-20260101-120000.zip", "size": 1024, "modified": "2026-01-01T12:00:00Z", "is_dir": False},
        {"name": "beidou-20260102-120000.zip", "size": 2048, "modified": "2026-01-02T12:00:00Z", "is_dir": False},
        {"name": "random-file.txt", "size": 100, "modified": "2026-01-03T00:00:00Z", "is_dir": False},  # 过滤
        {"name": "beidou-20260103-dir", "size": 0, "modified": "2026-01-03T00:00:00Z", "is_dir": True},  # 目录过滤
    ])

    result = await integ_mod.list_alist_backups(user=SimpleNamespace(id=1), db=None)

    names = [b["filename"] for b in result["backups"]]
    assert names == ["beidou-20260103-dir.zip" if False else "beidou-20260102-120000.zip", "beidou-20260101-120000.zip"]


@pytest.mark.asyncio
async def test_list_backups_with_named_root_uses_correct_path(monkeypatch):
    """root='/beidou' 时 list_dir 路径应为 /beidou/backup。"""
    _stub_config(monkeypatch, root="/beidou")
    fake = _patch_alist_list(monkeypatch, items=[])

    await integ_mod.list_alist_backups(user=SimpleNamespace(id=1), db=None)

    assert fake.list_calls == ["beidou/backup"]  # AlistClient._norm 去前导斜杠


@pytest.mark.asyncio
async def test_list_backups_empty(monkeypatch):
    """空目录或无匹配文件 → backups 为空列表。"""
    _stub_config(monkeypatch, root="/")
    _patch_alist_list(monkeypatch, items=[])

    result = await integ_mod.list_alist_backups(user=SimpleNamespace(id=1), db=None)
    assert result == {"backups": []}


@pytest.mark.asyncio
async def test_list_backups_alist_error_returns_400(monkeypatch):
    """Alist 报错 → 400，错误文案包含 AList 原始 message。"""
    from app.alist import AlistError

    _stub_config(monkeypatch, root="/")

    class _ErrClient:
        def __init__(self, base_url, username, password, transport=None):
            pass
        async def list_dir(self, path: str):
            raise AlistError("storage offline", status=500)

    monkeypatch.setattr(integ_mod, "AlistClient", _ErrClient)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await integ_mod.list_alist_backups(user=SimpleNamespace(id=1), db=None)
    assert exc.value.status_code == 400
    assert "storage offline" in exc.value.detail


# ---------- 下载 ----------


@pytest.mark.asyncio
async def test_download_backup_returns_zip_bytes(monkeypatch):
    """download 返回 StreamingResponse，Content-Disposition 是 attachment。"""
    from fastapi.responses import StreamingResponse

    _stub_config(monkeypatch, root="/")
    fake = _patch_alist_get_bytes(monkeypatch, payload=b"PK\x03\x04zip-content-here")

    resp = await integ_mod.download_alist_backup(
        filename="beidou-20260101-120000.zip", user=SimpleNamespace(id=1), db=None
    )

    assert isinstance(resp, StreamingResponse)
    assert resp.media_type == "application/zip"
    # Content-Disposition
    assert 'attachment' in resp.headers["Content-Disposition"]
    assert "beidou-20260101-120000.zip" in resp.headers["Content-Disposition"]
    # 调了 get_bytes + 路径正确
    assert fake.get_calls == ["/backup/beidou-20260101-120000.zip"]


@pytest.mark.asyncio
async def test_download_backup_named_root_path(monkeypatch):
    """root='/beidou' 时 get_bytes 路径应为 /beidou/backup/<filename>。"""
    _stub_config(monkeypatch, root="/beidou")
    fake = _patch_alist_get_bytes(monkeypatch, payload=b"x")

    await integ_mod.download_alist_backup(
        filename="beidou-20260101-120000.zip", user=SimpleNamespace(id=1), db=None
    )

    assert fake.get_calls == ["beidou/backup/beidou-20260101-120000.zip"]  # AlistClient._norm 去前导斜杠


@pytest.mark.asyncio
async def test_download_backup_rejects_path_traversal(monkeypatch):
    """filename 含 '/' 或 '..' → 400（防穿越）。"""
    from fastapi import HTTPException

    _stub_config(monkeypatch, root="/")
    _patch_alist_get_bytes(monkeypatch, payload=b"x")

    for bad in ("../etc/passwd", "..\\windows", "subdir/beidou-1.zip", "beidou-1.zip/../etc"):
        with pytest.raises(HTTPException) as exc:
            await integ_mod.download_alist_backup(filename=bad, user=SimpleNamespace(id=1), db=None)
        assert exc.value.status_code == 400, f"应拒绝 {bad}"


@pytest.mark.asyncio
async def test_download_backup_rejects_non_beidou_filename(monkeypatch):
    """filename 不符合 beidou-*.zip 规则 → 400。"""
    from fastapi import HTTPException

    _stub_config(monkeypatch, root="/")
    _patch_alist_get_bytes(monkeypatch, payload=b"x")

    for bad in ("malicious.zip", "beidou-2026.txt", "../beidou-2026.zip", "beidou-2026.tar.gz"):
        with pytest.raises(HTTPException) as exc:
            await integ_mod.download_alist_backup(filename=bad, user=SimpleNamespace(id=1), db=None)
        assert exc.value.status_code == 400, f"应拒绝 {bad}"


@pytest.mark.asyncio
async def test_download_backup_alist_error_returns_400(monkeypatch):
    """get_bytes 抛 AlistError → 400 透传 message。"""
    from app.alist import AlistError
    from fastapi import HTTPException

    _stub_config(monkeypatch, root="/")

    class _ErrClient:
        def __init__(self, base_url, username, password, transport=None):
            pass
        async def get_bytes(self, path: str):
            raise AlistError("file not found", status=404)

    monkeypatch.setattr(integ_mod, "AlistClient", _ErrClient)

    with pytest.raises(HTTPException) as exc:
        await integ_mod.download_alist_backup(
            filename="beidou-20260101-120000.zip", user=SimpleNamespace(id=1), db=None
        )
    assert exc.value.status_code == 400
    assert "file not found" in exc.value.detail


# ---------- AlistClient.get_bytes 单元 ----------


@pytest.mark.asyncio
async def test_alist_client_get_bytes_via_mock_transport():
    """AlistClient.get_bytes：先 POST /api/fs/get 拿 raw_url，再 GET raw_url 取字节。"""
    from app.alist import AlistClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"code": 200, "message": "ok", "data": {"token": "tok"}})
        if request.method == "POST" and request.url.path == "/api/fs/get":
            # 返回 raw_url（相对路径）
            return httpx.Response(200, json={"code": 200, "message": "ok", "data": {"raw_url": "/d/backup/beidou-2026.zip?sign=abc"}})
        if request.method == "GET" and request.url.path == "/d/backup/beidou-2026.zip":
            return httpx.Response(200, content=b"ZIP-CONTENT", headers={"content-type": "application/zip"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    transport = httpx.MockTransport(handler)
    client = AlistClient("https://alist.example.com", "u", "p", transport=transport)
    data = await client.get_bytes("/backup/beidou-2026.zip")
    assert data == b"ZIP-CONTENT"
