"""三处修复的回归测试：
- test_alist probe 改写到 backup 子目录（与 backup 端点同路径）
- POST /ai/models 接受 config_id（从 DB 读 key），并向后兼容旧传参
- 跨用户 config 404
"""

import io
import json
import zipfile
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.ai as ai_mod
import app.routers.integrations as integ_mod
from app.models import AIConfig, Base, IntegrationConfig, Novel, User


# ---------- 共享 helper：环境 ----------


class _AnonUser:
    def __init__(self, user_id: int, role: str = "author"):
        self.id = user_id
        self.role = role


def _patch_alist(monkeypatch, *, login_ok: bool = True, put_paths: list | None = None):
    """Mock AlistClient：login 默认成功；put/remove 记录调用路径。"""
    recorded = {"puts": [], "removes": []}

    class _FakeClient:
        def __init__(self, base_url, username, password, transport=None):
            self._base = base_url

        async def login(self):
            if not login_ok:
                from app.alist import AlistError

                raise AlistError(401, "wrong username or password")

        async def test(self):
            await self.login()

        async def exists(self, path: str) -> bool:
            return True

        async def ensure_dirs(self, path: str) -> None:
            return None

        async def put(self, path: str, data, content_type: str = ""):
            recorded["puts"].append((path, content_type))

        async def remove(self, path: str) -> None:
            recorded["removes"].append(path)

    monkeypatch.setattr(integ_mod, "AlistClient", _FakeClient)
    return recorded


def _stub_config(monkeypatch, *, url="https://alist.example.com", user="u", pwd="p", root="/"):
    async def fake_get_config(user, db):
        return SimpleNamespace(
            alist_url=url, alist_username=user, alist_password=pwd, alist_root=root
        )

    monkeypatch.setattr(integ_mod, "_get_config", fake_get_config)


# ---------- test_alist probe 路径：写到 backup 子目录 ----------


@pytest.mark.asyncio
async def test_alist_probe_writes_to_backup_subdir(monkeypatch, tmp_path):
    """t7-fix1: test_alist 写入测试文件路径应在 backup 子目录，而非根目录。
    这样 AList 账户基本路径是「根只读、子目录可写」时，test 与 backup 行为一致。
    """
    recorded = _patch_alist(monkeypatch)
    _stub_config(monkeypatch, root="/")

    result = await integ_mod.test_alist(user=_AnonUser(1), db=None)

    assert result["ok"] is True
    assert len(recorded["puts"]) == 1
    path, ctype = recorded["puts"][0]
    assert "backup" in path and ".beidou-write-test" in path, f"probe 路径应在 backup 子目录，实际：{path}"
    # 兜底确认不是写到根目录
    assert path != "/.beidou-write-test"
    # 删除也调了一次
    assert len(recorded["removes"]) == 1


@pytest.mark.asyncio
async def test_alist_probe_with_named_root_writes_under_root_backup(monkeypatch):
    """当 root 是具体子目录（如 /beidou）时，probe 路径是 /beidou/backup/.beidou-write-test。"""
    recorded = _patch_alist(monkeypatch)
    _stub_config(monkeypatch, root="/beidou")

    await integ_mod.test_alist(user=_AnonUser(1), db=None)

    path, _ = recorded["puts"][0]
    # alist 客户端的 _norm 会去前导斜杠——断言包含期望段即可
    assert "beidou" in path and "backup" in path and ".beidou-write-test" in path
    # 兜底：不应写到根（避免只读根导致测试失败但备份成功的不对称）
    assert path.count("/") >= 2  # 至少包含 backup 与 .beidou-write-test 两层


# ---------- /ai/models config_id 路径 ----------


@pytest_asyncio.fixture
async def db_with_user_config():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        c = AIConfig(
            user_id=u.id,
            name="MyConfig",
            base_url="https://api.example.com",
            api_key="sk-test-1234567890",
            model="gpt-4",
        )
        s.add(c)
        await s.commit()
        await s.refresh(c)
        yield s, u, c


@pytest_asyncio.fixture
async def db_with_user_no_key_config():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        c = AIConfig(user_id=u.id, name="NoKey", base_url="https://api.example.com", api_key="", model="gpt-4")
        s.add(c)
        await s.commit()
        await s.refresh(c)
        yield s, u, c


@pytest_asyncio.fixture
async def db_with_two_users():
    """两个用户各一个 config，验证跨用户隔离。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        u1 = User(username="u1", password_hash="x", role="author")
        u2 = User(username="u2", password_hash="x", role="author")
        s.add_all([u1, u2])
        await s.flush()
        c1 = AIConfig(user_id=u1.id, name="C1", base_url="https://a.example.com", api_key="k1", model="m1")
        c2 = AIConfig(user_id=u2.id, name="C2", base_url="https://b.example.com", api_key="k2", model="m2")
        s.add_all([c1, c2])
        await s.commit()
        await s.refresh(c1)
        await s.refresh(c2)
        yield s, u1, c1, u2, c2


def _patch_provider(monkeypatch, models_response: dict | None = None, status: int = 200):
    """Mock httpx 调用 /v1/models，让 list_models 走通。"""
    captured = {"url": None, "auth": None}

    def fake_get(url, headers=None, **kwargs):
        captured["url"] = url
        captured["auth"] = headers.get("Authorization") if headers else None
        return httpx.Response(status, json=models_response or {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5"}]})

    class _FakeAsyncClient:
        def __init__(self, timeout=None, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url, headers=None, **kwargs):
            return fake_get(url, headers)

    import app.routers.ai as ai_mod_inner

    monkeypatch.setattr(ai_mod_inner.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


# ---------- models config_id 路径 ----------


@pytest.mark.asyncio
async def test_models_uses_config_id_from_db(monkeypatch, db_with_user_config):
    """传 config_id 时，url/authorization 都用 DB 里的 base_url + api_key。"""
    db, user, config = db_with_user_config
    captured = _patch_provider(monkeypatch, models_response={"data": [{"id": "gpt-4"}]})

    result = await ai_mod.list_models(
        data=ai_mod.ModelsIn(config_id=config.id),
        user=user,
        db=db,
    )

    assert result == {"models": ["gpt-4"]}
    assert captured["url"] == "https://api.example.com/v1/models"
    assert captured["auth"] == "Bearer sk-test-1234567890"


@pytest.mark.asyncio
async def test_models_legacy_base_url_and_api_key_still_works(monkeypatch, db_with_user_config):
    """向后兼容：传 base_url + api_key（不传 config_id）仍可工作（dry-run 模式）。"""
    db, user, _ = db_with_user_config
    captured = _patch_provider(monkeypatch)

    result = await ai_mod.list_models(
        data=ai_mod.ModelsIn(base_url="https://dry-run.example.com", api_key="dry-key"),
        user=user,
        db=db,
    )

    assert result == {"models": ["gpt-3.5", "gpt-4"]}
    assert captured["url"] == "https://dry-run.example.com/v1/models"
    assert captured["auth"] == "Bearer dry-key"


@pytest.mark.asyncio
async def test_models_missing_everything_400(monkeypatch, db_with_user_config):
    """啥都没传 → 400。"""
    db, user, _ = db_with_user_config
    _patch_provider(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await ai_mod.list_models(data=ai_mod.ModelsIn(), user=user, db=db)
    assert exc.value.status_code == 400
    assert "config_id" in exc.value.detail or "base_url" in exc.value.detail


@pytest.mark.asyncio
async def test_models_config_without_key_400(monkeypatch, db_with_user_no_key_config):
    """config_id 存在但 DB 里没存 key，且 base_url/api_key 也没传 → 400。"""
    db, user, config = db_with_user_no_key_config
    _patch_provider(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await ai_mod.list_models(data=ai_mod.ModelsIn(config_id=config.id), user=user, db=db)
    assert exc.value.status_code == 400
    assert "API Key" in exc.value.detail or "没有保存" in exc.value.detail


@pytest.mark.asyncio
async def test_models_user_args_override_config_db_values(monkeypatch, db_with_user_config):
    """config_id 配合 base_url/api_key 时，user 传的覆盖 DB 里的——前端支持"已存配置但临时改 key 试拉"场景。"""
    db, user, config = db_with_user_config
    captured = _patch_provider(monkeypatch)

    result = await ai_mod.list_models(
        data=ai_mod.ModelsIn(
            config_id=config.id,
            base_url="https://override.example.com",
            api_key="override-key",
        ),
        user=user,
        db=db,
    )

    assert result["models"]
    # 用户传的 base_url 优先
    assert captured["url"] == "https://override.example.com/v1/models"
    # 用户传的 api_key 优先
    assert captured["auth"] == "Bearer override-key"


@pytest.mark.asyncio
async def test_models_cross_user_config_404(monkeypatch, db_with_two_users):
    """u1 不能用 u2 的 config_id（→ 404 假装不存在）。"""
    db, u1, c1, u2, c2 = db_with_two_users
    _patch_provider(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await ai_mod.list_models(data=ai_mod.ModelsIn(config_id=c2.id), user=u1, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_models_nonexistent_config_id_404(monkeypatch, db_with_user_config):
    """不存在的 config_id → 404。"""
    db, user, _ = db_with_user_config
    _patch_provider(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        await ai_mod.list_models(data=ai_mod.ModelsIn(config_id=99999), user=user, db=db)
    assert exc.value.status_code == 404
