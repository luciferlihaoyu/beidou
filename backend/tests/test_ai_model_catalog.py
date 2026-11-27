"""模型清单改造：`POST /api/ai/test` 支持 config_id 兜底（前端逐行「测试」用它）。

被钉死的行为（契约）：
1. 传 ``{config_id, model}`` → base_url / api_key 从 **DB 已保存配置**取，
   且请求体里的 ``model`` 就是用户点的那一个（不是配置里的旧 model）——
   这正是「78 个模型逐个测试」的唯一通路，写错就等于整条链路根本测不到模型。
2. 别人的 ``config_id`` → 404（不泄露资源是否存在，也不许借用他人 key）。
3. ``config_id`` 缺省且 base_url/api_key 也全空 → 400，且**一次外呼都不许发**
   （否则会拿着空 key 去撞上游，把 401 写成「Key 无效」误导用户）。
4. 向后兼容：``{base_url, api_key, model}`` 的旧用法照旧成功。
   另加一条：显式传入的 api_key 优先于 DB 值（用户拿新 key 先试再存）。

测试口径（沿用 tests/test_library_search_scope.py）：临时文件 SQLite +
``dependency_overrides[get_db]`` + ``httpx.ASGITransport`` 打**真实路由**，认证走真实
JWT 签发链路；**出站 HTTP 用 httpx.MockTransport 拦死**——不打真实网络、不用任何真实
key、也不给上游留任何依赖。``app.routers.ai`` 里是 ``import httpx`` 后 ``httpx.AsyncClient(...)``，
故这里把**该模块的 httpx 名称**换成一个只带 MockTransport 的 shim，
而不是改全局 httpx（避免污染同进程的其它测试）。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps:/data/dsh/北斗/beidou/backend \\
      python3 -m pytest tests/test_ai_model_catalog.py -q
"""

import json
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import get_db
from app.models import AIConfig, Base, User
from app.security import create_token

# 测试专用假 key：故意写成显眼的不可能值，杜绝与任何真实凭据混淆
DB_KEY = "sk-test-db-key-not-real"
DRY_KEY = "sk-test-dry-key-not-real"

BASE_URL_DB = "https://endpoint.test.invalid"  # .invalid 由 RFC 2606 保留，永不可解析
BASE_URL_DRY = "https://dry.test.invalid"


def _chat_ok(model: str = "echo-model") -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": "连接成功"}}],
    }


class _HttpxShim:
    """替身 httpx：``AsyncClient`` 一定挂 MockTransport，异常类原样转发。

    只注入到 ``app.routers.ai`` 模块的命名空间里，不改全局 httpx。
    """

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.response_factory = lambda request: httpx.Response(200, json=_chat_ok())
        self.HTTPError = httpx.HTTPError
        self.TimeoutException = httpx.TimeoutException

        shim = self

        class _Client(httpx.AsyncClient):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(shim._handle)
                super().__init__(*args, **kwargs)

        self.AsyncClient = _Client

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response_factory(request)


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """两名用户 + 各自的已保存配置 + 真实 token + MockTransport 出站档板。"""
    from app.main import app
    from app.routers import ai as ai_router

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        owner = User(username="catalog_owner", password_hash="x", role="author")
        other = User(username="catalog_other", password_hash="x", role="author")
        s.add_all([owner, other])
        await s.flush()
        mine = AIConfig(
            user_id=owner.id,
            name="我的端点",
            base_url=BASE_URL_DB,
            api_key=DB_KEY,
            model="stored-default-model",
        )
        theirs = AIConfig(
            user_id=other.id,
            name="别人的端点",
            base_url=BASE_URL_DB,
            api_key=DB_KEY,
            model="stored-default-model",
        )
        # 盲区 B 的靶子：库里 key 是空串的配置（用户新建时没填 key 就保存了）
        no_key = AIConfig(
            user_id=owner.id,
            name="空 Key 端点",
            base_url=BASE_URL_DB,
            api_key="",
            model="stored-default-model",
        )
        s.add_all([mine, theirs, no_key])
        await s.commit()
        ids = SimpleNamespace(
            owner=owner.id,
            other=other.id,
            owner_name="catalog_owner",
            other_name="catalog_other",
            mine_config=mine.id,
            theirs_config=theirs.id,
            no_key_config=no_key.id,
        )

    async def fake_get_db():
        async with SL() as s:
            yield s

    shim = _HttpxShim()
    monkeypatch.setattr(ai_router, "httpx", shim)
    app.dependency_overrides[get_db] = fake_get_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    try:
        yield SimpleNamespace(
            ids=ids,
            shim=shim,
            client=client,
            token_owner=create_token(ids.owner, ids.owner_name),
            token_other=create_token(ids.other, ids.other_name),
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()  # 关键：防 aiosqlite worker 跨 loop 崩（同 test_library_search_scope）


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- 1. config_id 兜底


@pytest.mark.asyncio
async def test_config_id_fills_credentials_and_sends_clicked_model(env):
    """传 {config_id, model}：URL/key 取自 DB，请求体里的 model 是点的那一个。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"config_id": env.ids.mine_config, "model": "model-from-catalog"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["reply"] == "连接成功"
    # 返回值里的 model 优先取上游回显（供应商常有别名映射），故此处只断言非空
    assert body["model"]

    assert len(env.shim.requests) == 1, "应当且只应当发一次外呼"
    sent = env.shim.requests[0]
    # URL 来自 DB 的 base_url（而不是空串拼出来的相对地址）
    assert str(sent.url) == f"{BASE_URL_DB}/v1/chat/completions"
    # key 来自 DB
    assert sent.headers["authorization"] == f"Bearer {DB_KEY}"
    # model 是用户点的那一个，不是配置里存的 stored-default-model

    assert json.loads(sent.content)["model"] == "model-from-catalog"


@pytest.mark.asyncio
async def test_explicit_api_key_overrides_db_value(env):
    """显式传入的 api_key 优先于 DB 值（用户拿新 key 先试再保存的路径）。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"config_id": env.ids.mine_config, "api_key": DRY_KEY, "model": "m1"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 200, r.text
    assert env.shim.requests[0].headers["authorization"] == f"Bearer {DRY_KEY}"


# ---------------------------------------------------------------- 2. 越权


@pytest.mark.asyncio
async def test_other_users_config_id_is_404(env):
    """别人的 config_id → 404，且一次外呼都不许发（不能借他人 key）。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"config_id": env.ids.theirs_config, "model": "m1"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 404, r.text
    assert env.shim.requests == [], "越权请求绝不能真的打到上游"


@pytest.mark.asyncio
async def test_missing_config_id_is_404(env):
    """不存在的 config_id 与越权同形：404（不区分，避免探测配置是否存在）。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"config_id": 999999, "model": "m1"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 404, r.text
    assert env.shim.requests == []


# ---------------------------------------------------------------- 3. 全空


@pytest.mark.asyncio
async def test_no_source_params_is_400_and_sends_nothing(env):
    """三个来源参数全空 → 400，且零外呼。"""
    r = await env.client.post(
        "/api/ai/test", json={"model": "m1"}, headers=_auth(env.token_owner)
    )
    assert r.status_code == 400, r.text
    assert "config_id" in r.json()["detail"]
    assert env.shim.requests == [], "缺凭据时不许发外呼"


@pytest.mark.asyncio
async def test_blank_strings_are_treated_as_absent(env):
    """显式传空串等价于「没传」——不能拼出 http:///v1/chat/completions 去撞上游。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"base_url": "", "api_key": "   ", "model": "m1"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 400, r.text
    assert env.shim.requests == []


# ---------------------------------------------------------------- 3b. 库里没有 key


@pytest.mark.asyncio
async def test_config_with_empty_key_is_400_and_sends_nothing(env):
    """配置存在但库里没有 key、用户也没给新 key → 400 且**零外呼**。

    为什么必须零外呼：发出去就是拿空 key 撞上游，回来的 401 会被显示成
    「API Key 无效或没有权限」——把「配置本身没存 key」误报成「你的 key 是错的」，
    用户会去反复重填一个本来就正确的 key。文案也要点明是配置缺 key（m4）。
    """
    r = await env.client.post(
        "/api/ai/test",
        json={"config_id": env.ids.no_key_config, "model": "m1"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "API Key" in detail, detail
    assert "config_id" not in detail, f"不能再用那句误导人的通用文案：{detail}"
    assert env.shim.requests == [], "库里没 key 时不许真的发出去"


@pytest.mark.asyncio
async def test_config_with_empty_key_accepts_explicit_new_key(env):
    """库里没 key，但用户这次填了新 key（先试再存的路径）→ 照常发，用新 key。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"config_id": env.ids.no_key_config, "api_key": DRY_KEY, "model": "m1"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 200, r.text
    assert env.shim.requests[0].headers["authorization"] == f"Bearer {DRY_KEY}"


# ---------------------------------------------------------------- 4. 旧用法兼容


@pytest.mark.asyncio
async def test_legacy_base_url_and_api_key_still_works(env):
    """向后兼容：{base_url, api_key, model} 的老用法照旧成功。"""
    r = await env.client.post(
        "/api/ai/test",
        json={"base_url": BASE_URL_DRY + "/v1", "api_key": DRY_KEY, "model": "legacy-model"},
        headers=_auth(env.token_owner),
    )
    assert r.status_code == 200, r.text
    sent = env.shim.requests[0]
    # /v1 后缀被规范化掉，不会拼成 /v1/v1/chat/completions
    assert str(sent.url) == f"{BASE_URL_DRY}/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {DRY_KEY}"

    assert json.loads(sent.content)["model"] == "legacy-model"


@pytest.mark.asyncio
async def test_saved_config_test_route_still_works(env):
    """``POST /api/ai/configs/{id}/test``（列表页「测试」）未被新签名打断。"""
    r = await env.client.post(
        f"/api/ai/configs/{env.ids.mine_config}/test", json={}, headers=_auth(env.token_owner)
    )
    assert r.status_code == 200, r.text

    # 这条路用配置里存的 model
    assert json.loads(env.shim.requests[0].content)["model"] == "stored-default-model"
