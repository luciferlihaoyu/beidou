"""t6：项目输出字段（璇玑自动同步的状态显示与开关）回归测试：

- 详情/列表响应（_project_out 共用）含 last_kb_sync_at（字符串，初始 ""）与
  auto_kb_sync（布尔，初始 True）——前端「上次同步/未同步」显示与自动同步开关的数据来源。
- 项目设置更新（ProjectSettingsIn）接受 auto_kb_sync 并落库——前端开关切换的数据通路；
  last_kb_sync_at 是系统留痕字段，不走设置更新（只由同步成功代码写入）。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.ai_factory as factory
from app.models import AiProject, Base, User


# ---------- 共享 helper：内存库 + 造数（与 test_kb_sync 同款夹具，互不干扰） ----------


@pytest_asyncio.fixture
async def sl():
    """独立内存库（Base.metadata.create_all 直接带新列，覆盖新库迁移口径）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_project(sl: async_sessionmaker) -> tuple[User, AiProject]:
    async with sl() as db:
        user = User(username="t6-tester", password_hash="x")
        db.add(user)
        await db.flush()
        project = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙")
        db.add(project)
        await db.commit()
        return user, project


# ---------- 输出字段：详情 / 列表 ----------


@pytest.mark.asyncio
async def test_project_detail_outputs_kb_sync_fields(sl):
    """详情响应含 last_kb_sync_at（初始空串）与 auto_kb_sync（初始 True）。"""
    user, p = await _make_project(sl)

    async with sl() as db:
        out = await factory.get_project(p.id, user, db)

    assert isinstance(out["last_kb_sync_at"], str)
    assert out["last_kb_sync_at"] == ""
    assert out["auto_kb_sync"] is True


@pytest.mark.asyncio
async def test_project_list_outputs_kb_sync_fields(sl):
    """列表响应同款字段（与详情共用 _project_out，一并覆盖）。"""
    user, p = await _make_project(sl)

    async with sl() as db:
        out = await factory.list_projects(user, db)

    assert len(out) == 1
    assert out[0]["last_kb_sync_at"] == ""
    assert out[0]["auto_kb_sync"] is True


# ---------- 更新通路：开关可落库 ----------


@pytest.mark.asyncio
async def test_update_project_persists_auto_kb_sync(sl):
    """设置更新接受 auto_kb_sync 并落库（前端开关数据通路）；last_kb_sync_at 不受影响。"""
    from app.routers.ai_factory import ProjectSettingsIn

    user, p = await _make_project(sl)

    async with sl() as db:
        out = await factory.update_project(p.id, ProjectSettingsIn(auto_kb_sync=False), user, db)

    assert out["auto_kb_sync"] is False
    async with sl() as db2:  # 独立 session 验证落库，避开 identity map 缓存
        p2 = await db2.get(AiProject, p.id)
        assert p2.auto_kb_sync is False
        assert p2.last_kb_sync_at == ""  # 留痕字段不随设置更新变动
