"""P0-1 同步加固：settings.py 删除路径（角色/世界观/伏笔）端到端归档 + 失败兜底。

settings.py 的角色/世界观/伏笔删除函数，原本把 `archive_to_recycle` 调用包在
`except Exception: pass` 里——任何归档失败都被静默吞掉，删除照常进行但废纸篓空着，
P0-1 修完 owner_id → user_id 之后，AttributeError 没了，但其他归档失败仍会丢档。

本文件验证：
1. 成功路径：删除角色/世界观/伏笔 → RecycleBin 各有 1 条归档，实体被删；
2. 失败路径：归档抛异常时 settings 模块 logger.error 被调用 + 删除仍进行（不阻断）。

范式沿用 tests/test_recycle_roundtrip.py：临时 SQLite + dependency_overrides[get_db]
+ dependency_overrides[get_current_user] 注入 _AnonUser 替身，避免走真实 JWT 链路。
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.recycle as recycle_mod
import app.routers.settings as st_mod
from app.deps import get_current_user, get_db
from app.main import app
from app.models import (
    Base,
    Character,
    Foreshadowing,
    Novel,
    RecycleBin,
    User,
    WorldviewEntry,
)


class _AnonUser:
    """E2E 依赖覆盖用的轻量用户替身：与 test_recycle_roundtrip 同款。"""

    def __init__(self, user_id: int, role: str = "author"):
        self.id = user_id
        self.role = role


async def _build_world(item_kind: str, name: str):
    """建 user+novel+一类条目，返回 (engine, SL, user_id, novel_id, obj_id)。

    单独一个内存 DB，不持久化（够用即销毁）。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        u = User(username=f"u_{item_kind}", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        if item_kind == "character":
            obj = Character(novel_id=n.id, name=name, role="主角", description="主角介绍")
        elif item_kind == "setting":
            obj = WorldviewEntry(novel_id=n.id, category="势力", title=name, content="设定内容")
        elif item_kind == "foreshadow":
            obj = Foreshadowing(
                novel_id=n.id, title=name, content="伏笔内容", status="未回收"
            )
        else:
            raise ValueError(item_kind)
        s.add(obj)
        await s.commit()
        await s.refresh(n)
        await s.refresh(obj)
        return engine, SL, u.id, n.id, obj.id


# ---------- 用例 1-3：成功路径（角色 / 世界观 / 伏笔 删除 → 废纸篓留档） ----------


@pytest.mark.asyncio
async def test_delete_character_archives_to_recycle():
    """删角色 → 废纸篓有 1 条 kind=character + 角色实体已被删（不阻断）。"""
    engine, SL, user_id, novel_id, character_id = await _build_world(
        "character", "小明"
    )

    async def _override_db():
        async with SL() as ss:
            yield ss

    def _override_user():
        return _AnonUser(user_id=user_id, role="author")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete(
                f"/api/novels/{novel_id}/settings/characters/{character_id}"
            )
            assert r.status_code == 200, f"删角色应 200，实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    async with SL() as s:
        assert await s.get(Character, character_id) is None, "角色应已被删"
        rows = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "character"))
        ).scalars().all()
        assert len(rows) == 1, f"废纸篓应有 1 条 character 归档，实际 {len(rows)}"
        entry = rows[0]
        assert entry.novel_id == novel_id
        payload = json.loads(entry.payload)
        assert payload["name"] == "小明"
        assert payload["description"] == "主角介绍"

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_worldview_archives_to_recycle():
    """删世界观条目 → 废纸篓有 1 条 kind=setting + 条目已被删。"""
    engine, SL, user_id, novel_id, wv_id = await _build_world("setting", "青云宗")

    async def _override_db():
        async with SL() as ss:
            yield ss

    def _override_user():
        return _AnonUser(user_id=user_id, role="author")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete(
                f"/api/novels/{novel_id}/settings/worldview/{wv_id}"
            )
            assert r.status_code == 200, f"删世界观应 200，实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    async with SL() as s:
        assert await s.get(WorldviewEntry, wv_id) is None, "世界观条目应已被删"
        rows = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "setting"))
        ).scalars().all()
        assert len(rows) == 1, f"废纸篓应有 1 条 setting 归档，实际 {len(rows)}"
        payload = json.loads(rows[0].payload)
        assert payload["title"] == "青云宗"

    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_foreshadow_archives_to_recycle():
    """删伏笔 → 废纸篓有 1 条 kind=foreshadow + 伏笔已被删。"""
    engine, SL, user_id, novel_id, fs_id = await _build_world("foreshadow", "剑上之血")

    async def _override_db():
        async with SL() as ss:
            yield ss

    def _override_user():
        return _AnonUser(user_id=user_id, role="author")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete(
                f"/api/novels/{novel_id}/settings/foreshadowings/{fs_id}"
            )
            assert r.status_code == 200, f"删伏笔应 200，实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    async with SL() as s:
        assert await s.get(Foreshadowing, fs_id) is None, "伏笔应已被删"
        rows = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "foreshadow"))
        ).scalars().all()
        assert len(rows) == 1, f"废纸篓应有 1 条 foreshadow 归档，实际 {len(rows)}"
        payload = json.loads(rows[0].payload)
        assert payload["title"] == "剑上之血"

    await engine.dispose()


# ---------- 用例 4：归档失败 log.error + 不阻断（settings 模块兜底） ----------


@pytest.mark.asyncio
async def test_settings_archive_failure_logs_and_does_not_block(monkeypatch):
    """settings.py 归档抛异常时，logger.error 被调用 + 实体仍被删（不阻断正常使用）。

    范式与 test_recycle_roundtrip.py 用例 6 完全一致：monkeypatch archive_to_recycle 抛异常 +
    spy settings logger.error + 验角色仍被删 + 验日志内容含 kind=character。
    """
    async def boom(*args, **kwargs):
        raise RuntimeError("模拟归档失败")

    monkeypatch.setattr(recycle_mod, "archive_to_recycle", boom)

    logged: list[str] = []

    class _SpyLogger:
        def error(self, msg, *args):
            logged.append(msg % args if args else msg)

    monkeypatch.setattr(st_mod, "logger", _SpyLogger())

    engine, SL, user_id, novel_id, character_id = await _build_world(
        "character", "测试角色"
    )

    async def _override_db():
        async with SL() as ss:
            yield ss

    def _override_user():
        return _AnonUser(user_id=user_id, role="author")

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.delete(
                f"/api/novels/{novel_id}/settings/characters/{character_id}"
            )
            # 归档失败但删除不阻断 → HTTP 仍 200
            assert r.status_code == 200, f"归档失败时删除应仍 200，实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    # 验证：error 日志已落 + 角色已被删
    assert any(
        "废纸篓归档失败" in m and "kind=character" in m for m in logged
    ), f"settings 模块应记 kind=character 的 error 日志，实际：{logged}"

    async with SL() as s:
        assert await s.get(Character, character_id) is None, "归档失败时角色仍应被删"

    await engine.dispose()
