"""规格轴 M3 缺钉：chapters.py update_chapter 的 kind=paragraph 归档失败日志分支
（chapters.py:262-268）此前没有任何测试钉住——既有 spy 只覆盖 _delete_one_chapter
的 kind=chapter（test_recycle_roundtrip.py 用例 6）。

本用例走**段落归档路径**：更新章节正文、删掉若干段落 → update_chapter 计算被删段落 →
调 archive_to_recycle(kind="paragraph")。monkeypatch 让它抛异常，spy `beidou.chapters`
logger，断言：
1. logger.error 被记录且含 `kind=paragraph`；
2. 章节保存本身不受影响（PUT 返回 200、content 更新为新值、已落库）。

这是对既有行为的钉（实现已 logger.error，且本次不许改 chapters.py）：测试落地即绿；
其 RED 由回退验证给出——把 chapters.py 段落日志分支退回 `except Exception: pass`，
本用例即变红（证明它真能抓住「段落归档失败被静默吞掉」的回归）。

范式沿用 test_recycle_roundtrip.py 用例 6 / test_settings_recycle.py 用例 4：
monkeypatch recycle.archive_to_recycle 抛异常 + spy 模块 logger + tmp 文件库 +
dependency_overrides[get_db]/[get_current_user] + httpx.ASGITransport。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_paragraph_archive_log.py -q
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.chapters as ch_mod
import app.routers.recycle as recycle_mod
from app.deps import get_current_user, get_db
from app.main import app
from app.models import Base, Chapter, Novel, User
from app.search_fts import ensure_fts


class _AnonUser:
    """E2E 依赖覆盖用的轻量用户替身：只需 id / role。"""

    def __init__(self, user_id: int, role: str = "author"):
        self.id = user_id
        self.role = role


@pytest.mark.asyncio
async def test_paragraph_archive_failure_logs_kind_paragraph_and_does_not_block(
    tmp_path, monkeypatch
):
    """段落归档失败 → beidou.chapters 记 error（含 kind=paragraph）+ 章节保存不受影响。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'para_log.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_fts(conn)
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        u = User(username="para", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="NP", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        ch = Chapter(
            novel_id=n.id,
            title="多段章",
            content="<p>段落一</p><p>段落二</p><p>段落三</p>",
            word_count=50,  # 故意偏大：改短正文使 delta<0，绕开 record_writing 分支
            sort_order=0,
            status="draft",
            tags="[]",
        )
        s.add(ch)
        await s.commit()
        user_id, novel_id, chapter_id = u.id, n.id, ch.id

    # 1) 让 archive_to_recycle 抛异常，模拟「段落归档」失败（update_chapter 内部是
    #    函数级 `from ..routers.recycle import archive_to_recycle`，调用时才读模块属性，
    #    所以 patch recycle_mod.archive_to_recycle 即可命中段落归档路径）
    async def boom(*args, **kwargs):
        raise RuntimeError("模拟段落归档失败")

    monkeypatch.setattr(recycle_mod, "archive_to_recycle", boom)

    # 2) spy logger 确定性捕获 chapters 模块日志（不依赖 logging 配置）
    logged: list[str] = []

    class _SpyLogger:
        def error(self, msg, *args):
            logged.append(msg % args if args else msg)

        def warning(self, msg, *args):
            pass

        def info(self, msg, *args):
            pass

    monkeypatch.setattr(ch_mod, "logger", _SpyLogger())

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
            # 更新正文：删掉「段落二」「段落三」→ archived_paragraphs 非空 → 触发段落归档路径
            r = await c.put(
                f"/api/novels/{novel_id}/chapters/{chapter_id}",
                json={"content": "<p>段落一</p>"},
            )
            assert r.status_code == 200, (
                f"段落归档失败不应阻断章节保存，PUT 应 200，实际 {r.status_code}: {r.text}"
            )
            body = r.json()
            assert body["content"] == "<p>段落一</p>", f"正文应更新为新值，实际 {body.get('content')}"
    finally:
        app.dependency_overrides.clear()

    # 断言：error 日志已落且含 kind=paragraph（M3 缺钉的核心）
    assert any("kind=paragraph" in m for m in logged), (
        f"段落归档失败应记含 kind=paragraph 的 error 日志（M3），实际日志：{logged}"
    )
    assert any("废纸篓归档失败" in m for m in logged), (
        f"日志应含「废纸篓归档失败」，实际：{logged}"
    )

    # 独立 session 校验：章节内容确实更新并落库（保存未受归档失败影响）
    async with SL() as s:
        ch = await s.get(Chapter, chapter_id)
        assert ch is not None
        assert ch.content == "<p>段落一</p>", f"章节内容应更新为新值，实际 {ch.content}"
        # 归档失败被吞 → 不应留下 paragraph 废纸篓条目
        from app.models import RecycleBin

        para_entries = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "paragraph"))
        ).scalars().all()
        assert len(para_entries) == 0, f"归档抛异常时不应留下 paragraph 条目，实际 {len(para_entries)} 条"

    await engine.dispose()
