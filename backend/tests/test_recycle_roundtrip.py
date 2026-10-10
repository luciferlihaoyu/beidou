"""RecycleBin 端到端回归测试（P0 止血）。

病根：Novel 模型只有 user_id，全仓无 owner_id 属性，四处误用 owner_id 必抛 AttributeError——
- chapters.py `_get_novel_owner`：删章归档时被 `except: pass` 静默吞掉 → 章节物理删除、不可恢复
- recycle.py `restore_entry`：恢复接口直接 500
- pomodoro.py `complete_pomo` / `today_pomo`：带 novel_id 的番茄上报/查询直接 500

这些测试走真实删除/恢复/上报路径（而非直调无 bug 的 archive_to_recycle），
修复前 RED（归档缺失或 500），修复后 GREEN。E2E 用 httpx ASGITransport +
dependency_overrides（与 test_chapter_status.py 同模式），raise_app_exceptions=False
让未捕获异常落成 500 响应，与生产一致，便于断言状态码。
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.chapters as ch_mod
from app.deps import get_current_user, get_db
from app.main import app
from app.models import Base, Chapter, Novel, PomoLog, RecycleBin, User
from app.routers.recycle import archive_to_recycle


class _AnonUser:
    """E2E 依赖覆盖用的轻量用户替身：restore/pomodoro 只需 id / role。"""

    def __init__(self, user_id: int, role: str = "author"):
        self.id = user_id
        self.role = role


# ---------- 用例 1：删章 → 废纸篓留档（chapters.py:478 _get_novel_owner 病根回归） ----------


@pytest.mark.asyncio
async def test_delete_chapter_archives_to_recycle():
    """走真实 _delete_one_chapter 路径：章节被删且 RecycleBin 留下一条可恢复归档。

    修复前 _get_novel_owner 访问 n.owner_id 抛 AttributeError，被 except: pass 吞掉，
    章节直接物理删除、废纸篓无任何记录 → 本用例因归档数为 0 而失败（RED）。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        ch = Chapter(
            novel_id=n.id,
            title="第一章 起因",
            content="<p>正文内容</p>",
            word_count=4,
            sort_order=0,
            status="draft",
            tags="[]",
        )
        s.add(ch)
        await s.commit()
        await s.refresh(n)
        await s.refresh(ch)
        novel_id, chapter_id, user_id = n.id, ch.id, u.id

        # 真实删除路径（内部调用 archive_to_recycle，正是病根所在）
        await ch_mod._delete_one_chapter(s, n, ch)
        await s.commit()

        # 章节应已被删除
        assert await s.get(Chapter, chapter_id) is None

        # 废纸篓应留下 1 条 chapter 归档
        rows = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "chapter"))
        ).scalars().all()
        assert len(rows) == 1, f"废纸篓应有 1 条归档，实际 {len(rows)}（归档被静默吞掉）"
        entry = rows[0]
        assert entry.novel_id == novel_id
        assert entry.user_id == user_id
        # payload 完整保留正文/标题，可供恢复
        payload = json.loads(entry.payload)
        assert payload["content"] == "<p>正文内容</p>"
        assert payload["title"] == "第一章 起因"

    await engine.dispose()


# ---------- 用例 2：归档 → restore 返回 200 → 章节回来（recycle.py:141 病根回归） ----------


@pytest.mark.asyncio
async def test_restore_chapter_recovers_content(tmp_path):
    """归档一章 → HTTP POST /api/recycle/{id}/restore 返回 200 → 章节回来且 content 相同。

    修复前 restore_entry 访问 novel.owner_id 抛 AttributeError → 接口 500（RED）。
    归档用 archive_to_recycle 直接建（隔离删除路径），专测恢复环节。
    """
    db_file = tmp_path / "recycle_restore.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        await archive_to_recycle(
            s,
            user=u,
            novel_id=n.id,
            kind="chapter",
            name="第一章 起因",
            payload={
                "title": "第一章 起因",
                "content": "<p>正文内容</p>",
                "word_count": 4,
                "sort_order": 0,
                "volume_id": None,
                "status": "draft",
                "tags": "[]",
            },
        )
        await s.commit()
        user_id, novel_id = u.id, n.id
        entry_id = (await s.execute(select(RecycleBin))).scalars().first().id

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(user_id, "author")

    async def fake_get_db():
        async with SL() as s:
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            r = await c.post(f"/api/recycle/{entry_id}/restore")
            assert r.status_code == 200, f"restore 应 200，实际 {r.status_code}: {r.text}"
            body = r.json()
            assert body["ok"] is True
            assert body["kind"] == "chapter"
    finally:
        app.dependency_overrides.clear()

    # 独立 session 校验：章节回来、content 相同、废纸篓条目已清
    async with SL() as s:
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == novel_id))
        ).scalars().all()
        assert len(chapters) == 1, f"章节应被恢复，实际 {len(chapters)} 条"
        assert chapters[0].title == "第一章 起因"
        assert chapters[0].content == "<p>正文内容</p>"
        assert chapters[0].word_count == 4
        remaining = (await s.execute(select(RecycleBin))).scalars().all()
        assert len(remaining) == 0, "恢复后废纸篓条目应被删除"

    await engine.dispose()


# ---------- 用例 3：带 novel_id 的番茄完成上报不再 500（pomodoro.py:60 病根回归） ----------


@pytest.mark.asyncio
async def test_pomodoro_complete_with_novel_id_no_500(tmp_path):
    """带 novel_id 的番茄完成上报 → 不再 500，落一行 PomoLog。

    修复前 complete_pomo 访问 novel.owner_id 抛 AttributeError → 接口 500（RED）。
    """
    db_file = tmp_path / "pomo_complete.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.commit()
        user_id, novel_id = u.id, n.id

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(user_id, "author")

    async def fake_get_db():
        async with SL() as s:
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            r = await c.post(
                "/api/pomodoro/complete",
                json={"novel_id": novel_id, "phase": "write", "duration_min": 25},
            )
            assert r.status_code != 500, f"带 novel_id 不应再 500：{r.text}"
            assert r.status_code == 200, f"应 200，实际 {r.status_code}: {r.text}"
            assert r.json()["ok"] is True
    finally:
        app.dependency_overrides.clear()

    async with SL() as s:
        logs = (
            await s.execute(select(PomoLog).where(PomoLog.user_id == user_id))
        ).scalars().all()
        assert len(logs) == 1
        assert logs[0].novel_id == novel_id
        assert logs[0].phase == "write"

    await engine.dispose()


# ---------- 用例 4：带 novel_id 的番茄今日查询不再 500（pomodoro.py:96 病根回归） ----------


@pytest.mark.asyncio
async def test_pomodoro_today_with_novel_id_no_500(tmp_path):
    """带 novel_id 的番茄今日统计查询 → 不再 500（覆盖 pomodoro.py:96 第二处病根）。"""
    db_file = tmp_path / "pomo_today.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.commit()
        user_id, novel_id = u.id, n.id

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(user_id, "author")

    async def fake_get_db():
        async with SL() as s:
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            r = await c.get("/api/pomodoro/today", params={"novel_id": novel_id})
            assert r.status_code != 500, f"带 novel_id 不应再 500：{r.text}"
            assert r.status_code == 200, f"应 200，实际 {r.status_code}: {r.text}"
            body = r.json()
            # 未上报过任何番茄 → 今日/累计均为 0（不断言具体日期，避免时间依赖）
            assert body["today"] == 0
            assert body["total"] == 0
    finally:
        app.dependency_overrides.clear()

    await engine.dispose()


# ---------- 用例 5：admin 可代恢复他人小说下的条目（recycle.py:141 admin 语义） ----------


@pytest.mark.asyncio
async def test_admin_can_restore_entry_under_foreign_novel(tmp_path):
    """admin 代恢复他人小说下的废纸篓条目（与 get_owned_novel 的 admin 语义一致）。

    修复前 restore_entry 访问 novel.owner_id 抛 AttributeError → 500；
    修复后 admin 即便 novel.user_id != admin.id 也应放行 → 200，章节在该小说下重建。
    """
    db_file = tmp_path / "recycle_admin.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        owner = User(username="owner", password_hash="x", role="author")
        admin = User(username="admin", password_hash="x", role="admin")
        s.add_all([owner, admin])
        await s.flush()
        n = Novel(user_id=owner.id, title="别人的书", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        # 归档条目归 admin（user_id=admin.id），但指向的小说归 owner
        await archive_to_recycle(
            s,
            user=admin,
            novel_id=n.id,
            kind="chapter",
            name="第一章",
            payload={
                "title": "第一章",
                "content": "<p>admin 代恢复</p>",
                "word_count": 5,
                "sort_order": 0,
                "volume_id": None,
                "status": "draft",
                "tags": "[]",
            },
        )
        await s.commit()
        admin_id, novel_id = admin.id, n.id
        entry_id = (await s.execute(select(RecycleBin))).scalars().first().id

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(admin_id, "admin")

    async def fake_get_db():
        async with SL() as s:
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            r = await c.post(f"/api/recycle/{entry_id}/restore")
            assert r.status_code == 200, f"admin 代恢复应 200，实际 {r.status_code}: {r.text}"
            assert r.json()["ok"] is True
    finally:
        app.dependency_overrides.clear()

    async with SL() as s:
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == novel_id))
        ).scalars().all()
        assert len(chapters) == 1, f"admin 代恢复后章节应重建，实际 {len(chapters)} 条"
        assert chapters[0].content == "<p>admin 代恢复</p>"

    await engine.dispose()


# ---------- 用例 6：归档失败时记 error 日志 + 不阻断删除（chapters.py:303-304 验收） ----------


@pytest.mark.asyncio
async def test_delete_chapter_archive_failure_logs_and_does_not_block(monkeypatch):
    """归档抛异常时，_delete_one_chapter 应：记 error 日志 + 不阻断删除（章节仍被删）。

    对应验收标准：chapters.py 归档失败从静默 `except: pass` 改为 logger.error，
    且不打断正常删除。用 spy logger 替换模块 logger，确定性捕获日志（不依赖 logging 配置）。
    """
    import app.routers.recycle as recycle_mod

    # 1) 让 archive_to_recycle 抛异常，模拟归档失败
    async def boom(*args, **kwargs):
        raise RuntimeError("模拟归档失败")

    monkeypatch.setattr(recycle_mod, "archive_to_recycle", boom)

    # 2) spy logger 捕获 chapters 模块的 error 日志
    logged: list[str] = []

    class _SpyLogger:
        def error(self, msg, *args):
            logged.append(msg % args if args else msg)

    monkeypatch.setattr(ch_mod, "logger", _SpyLogger())

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        ch = Chapter(novel_id=n.id, title="c", content="<p>x</p>", word_count=1, sort_order=0)
        s.add(ch)
        await s.commit()
        await s.refresh(n)
        await s.refresh(ch)
        chapter_id = ch.id

        # 归档失败，但删除不应被阻断
        await ch_mod._delete_one_chapter(s, n, ch)
        await s.commit()

        # 章节仍被删除（不阻断正常使用）
        assert await s.get(Chapter, chapter_id) is None
        # error 日志已落（不再静默 pass）
        assert any("废纸篓归档失败" in m for m in logged), f"归档失败应记 error 日志，实际日志：{logged}"

    await engine.dispose()
