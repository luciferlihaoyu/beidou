"""AlistClient 单元测试：通过 httpx.MockTransport 注入假 AList 服务，零真实网络。

不启动 FastAPI app、不触碰真实 DATA_DIR，直接实例化 AlistClient。
"""

import urllib.parse

import httpx
import pytest

from app.alist import AlistClient, AlistError

BASE = "https://alist.example.com"


def envelope(code: int = 200, message: str = "success", data: object = None) -> dict:
    """构造 AList 标准响应信封。"""
    return {"code": code, "message": message, "data": data}


def make_client(handler) -> AlistClient:
    return AlistClient(BASE, "user", "pass", transport=httpx.MockTransport(handler))


class FakeAlist:
    """记录请求并按脚本应答的假 AList：login 发 token，业务接口校验 token。"""

    def __init__(self, *, revoked_tokens: set[str] | None = None, login_code: int = 200):
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.login_count = 0
        self.revoked = revoked_tokens or set()
        self.login_code = login_code

    @property
    def token(self) -> str:
        return f"tok-{self.login_count}"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        self.requests.append(request)
        path = request.url.path
        body: dict = {}
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = {}
        # 所有请求（含登录）都记录 body，保持与 requests 一一对应
        self.bodies.append(body)
        if path == "/api/auth/login":
            self.login_count += 1
            if self.login_code != 200:
                return httpx.Response(200, json=envelope(self.login_code, "wrong username or password"))
            return httpx.Response(200, json=envelope(data={"token": self.token}))
        auth = request.headers.get("Authorization")
        if auth is None or auth == "" or auth in self.revoked:
            return httpx.Response(200, json=envelope(401, "token is invalid"))
        if path == "/api/fs/list":
            return httpx.Response(200, json=envelope(data={"content": [{"name": "backup", "is_dir": True}], "total": 1}))
        if path == "/api/fs/get":
            obj_path = body.get("path", "")
            if obj_path in ("/beidou", "/beidou/backup", "/beidou/exists-dir"):
                return httpx.Response(200, json=envelope(data={"name": obj_path.rsplit("/", 1)[-1], "is_dir": True}))
            return httpx.Response(200, json=envelope(500, f"failed get object: object not found"))
        if path == "/api/fs/mkdir":
            if body.get("path") == "/beidou/exists-dir":
                return httpx.Response(200, json=envelope(500, "failed mkdir: dir exists"))
            return httpx.Response(200, json=envelope())
        if path in ("/api/fs/put", "/api/fs/remove"):
            return httpx.Response(200, json=envelope())
        return httpx.Response(404, json=envelope(404, "not found"))


# ---------- 登录与鉴权 ----------


@pytest.mark.asyncio
async def test_login_caches_token_and_subsequent_requests_carry_it():
    al = FakeAlist()
    client = make_client(al)

    await client.login()
    assert client._token == "tok-1"

    await client.list_dir("/beidou")
    # 只登录一次；后续请求头携带缓存 token
    assert al.login_count == 1
    fs_req = al.requests[-1]
    assert fs_req.url.path == "/api/fs/list"
    assert fs_req.headers["Authorization"] == "tok-1"


@pytest.mark.asyncio
async def test_login_failure_raises_with_upstream_message():
    al = FakeAlist(login_code=403)
    client = make_client(al)

    with pytest.raises(AlistError) as excinfo:
        await client.login()
    assert "wrong username or password" in str(excinfo.value)


@pytest.mark.asyncio
async def test_expired_token_triggers_relogin_and_retry():
    # 预先把 tok-1 列入吊销名单：首次请求 401 → 自动重登（tok-2）→ 重试成功
    al = FakeAlist(revoked_tokens={"tok-1"})
    client = make_client(al)

    content = await client.list_dir("/beidou")
    assert [item["name"] for item in content] == ["backup"]
    assert al.login_count == 2  # 初始登录 + 过期重登
    assert al.requests[-1].headers["Authorization"] == "tok-2"


@pytest.mark.asyncio
async def test_network_error_wrapped_as_alist_error():
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(broken)
    with pytest.raises(AlistError) as excinfo:
        await client.test()
    assert "无法连接 AList" in str(excinfo.value)
    assert "ConnectError" in str(excinfo.value)


# ---------- 目录操作 ----------


@pytest.mark.asyncio
async def test_exists_true_for_200_and_false_for_object_not_found():
    al = FakeAlist()
    client = make_client(al)

    assert await client.exists("/beidou") is True
    assert await client.exists("/nope") is False


@pytest.mark.asyncio
async def test_exists_reraises_unrelated_errors():
    class AlwaysBoom(FakeAlist):
        def __call__(self, request: httpx.Request):
            self.requests.append(request)
            if request.url.path == "/api/auth/login":
                self.login_count += 1
                return httpx.Response(200, json=envelope(data={"token": self.token}))
            return httpx.Response(200, json=envelope(500, "storage disabled"))

    client = make_client(AlwaysBoom())
    with pytest.raises(AlistError):
        await client.exists("/anything")


@pytest.mark.asyncio
async def test_mkdir_idempotent_when_dir_exists():
    al = FakeAlist()
    client = make_client(al)
    await client.mkdir("/beidou/exists-dir")  # 上游报 dir exists 也视为成功（exists 复核确认目录在）
    # 发出过 mkdir 请求；末尾多一次 exists 复核请求
    assert any(r.url.path == "/api/fs/mkdir" for r in al.requests)
    assert al.requests[-1].url.path == "/api/fs/get"


@pytest.mark.asyncio
async def test_mkdir_reraises_when_parent_missing():
    """上游报 "parent does not exist"（含 exist 但目录并未建成）时必须抛出，不能误吞成成功。"""

    class ParentMissingAlist(FakeAlist):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            import json

            path = request.url.path
            if path == "/api/auth/login":
                self.login_count += 1
                return httpx.Response(200, json=envelope(data={"token": self.token}))
            body = json.loads(request.content) if request.content else {}
            self.requests.append(request)
            self.bodies.append(body)
            auth = request.headers.get("Authorization")
            if not auth or auth in self.revoked:
                return httpx.Response(200, json=envelope(401, "token is invalid"))
            if path == "/api/fs/mkdir":
                return httpx.Response(200, json=envelope(500, "mkdir: parent does not exist"))
            if path == "/api/fs/get":
                # 复核：目标目录并未真正建成
                return httpx.Response(200, json=envelope(500, "failed get object: object not found"))
            return httpx.Response(404, json=envelope(404, "not found"))

    client = make_client(ParentMissingAlist())
    with pytest.raises(AlistError) as excinfo:
        await client.mkdir("/beidou/orphan")
    assert "parent does not exist" in str(excinfo.value)


@pytest.mark.asyncio
async def test_ensure_dirs_skips_existing_first_level():
    """第一级（如受限账户的基本路径）已存在时不再对其发 mkdir。"""
    al = FakeAlist()
    client = make_client(al)

    await client.ensure_dirs("/beidou/newdir")

    mkdir_paths = [body.get("path") for r, body in zip(al.requests, al.bodies) if r.url.path == "/api/fs/mkdir"]
    # /beidou 已存在 → 跳过；仅 /beidou/newdir 需要创建
    assert mkdir_paths == ["/beidou/newdir"]


def _variant_handler(mode: str):
    """构造不存在判定的措辞变体响应。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json=envelope(data={"token": "tok"}))
        if mode == "http404":
            return httpx.Response(404, text="no route")  # 非 JSON 信封 → 按 HTTP 状态码映射 code=404
        if mode == "file-not-found":
            return httpx.Response(200, json=envelope(500, "failed get object: file not found"))
        raise AssertionError(f"unknown mode {mode}")

    return handler


@pytest.mark.asyncio
async def test_exists_false_on_http_404_without_envelope():
    client = AlistClient(BASE, "u", "p", transport=httpx.MockTransport(_variant_handler("http404")))
    assert await client.exists("/whatever") is False


@pytest.mark.asyncio
async def test_exists_false_on_not_found_wording_variant():
    client = AlistClient(BASE, "u", "p", transport=httpx.MockTransport(_variant_handler("file-not-found")))
    assert await client.exists("/whatever") is False


@pytest.mark.asyncio
async def test_ensure_dirs_short_circuits_when_root_exists():
    al = FakeAlist()
    client = make_client(al)

    await client.ensure_dirs("/beidou/backup")

    # /beidou/backup 已存在（FakeAlist 对该路径返回 200）→ 不发任何 mkdir
    assert not any(r.url.path == "/api/fs/mkdir" for r in al.requests)


@pytest.mark.asyncio
async def test_ensure_dirs_creates_level_by_level_when_missing():
    al = FakeAlist()
    client = make_client(al)

    await client.ensure_dirs("/fresh/a/b")

    mkdir_paths = [body.get("path") for r, body in zip(al.requests, al.bodies) if r.url.path == "/api/fs/mkdir"]
    assert mkdir_paths == ["/fresh", "/fresh/a", "/fresh/a/b"]


# ---------- 上传 / 删除 ----------


@pytest.mark.asyncio
async def test_put_request_shape():
    al = FakeAlist()
    client = make_client(al)

    target = "/beidou/backup/bei dou-1.zip"
    await client.put(target, b"payload", "application/zip")

    req = al.requests[-1]
    assert req.method == "PUT"
    assert req.url.path.endswith("/api/fs/put")
    assert req.headers["File-Path"] == urllib.parse.quote(target, safe="")
    assert req.headers["As-Task"] == "false"
    assert req.headers["Content-Type"] == "application/zip"
    assert req.content == b"payload"


@pytest.mark.asyncio
async def test_remove_splits_dir_and_names():
    al = FakeAlist()
    client = make_client(al)

    await client.remove("/beidou/backup/beidou-20250101-000000.zip")

    remove_calls = [
        (r, b) for r, b in zip(al.requests, al.bodies) if r.url.path == "/api/fs/remove"
    ]
    assert len(remove_calls) == 1
    _, body = remove_calls[0]
    assert body == {"dir": "/beidou/backup", "names": ["beidou-20250101-000000.zip"]}


@pytest.mark.asyncio
async def test_relative_paths_are_normalized_to_absolute():
    """路由层传入 strip 过的相对形式（如 "beidou/backup"）时，客户端应自动补前导 /。"""
    al = FakeAlist()
    client = make_client(al)

    assert await client.exists("beidou") is True
    assert al.bodies[-1] == {"path": "/beidou"}

    await client.mkdir("beidou/backup")
    assert al.bodies[-1] == {"path": "/beidou/backup"}

    await client.put("beidou/backup/a.zip", b"x")
    req = al.requests[-1]
    assert req.headers["File-Path"] == urllib.parse.quote("/beidou/backup/a.zip", safe="")


# ---------- 连通性测试 ----------


@pytest.mark.asyncio
async def test_test_method_logs_in_and_lists_root():
    al = FakeAlist()
    client = make_client(al)

    await client.test()

    paths = [r.url.path for r in al.requests]
    assert paths == ["/api/auth/login", "/api/fs/list"]
