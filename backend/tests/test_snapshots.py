"""章节快照/存稿点测试：service 层用真实内存 SQLite 验证去重/节流/防呆/diff 行为；
路由层用 monkeypatch 隔离 db 与依赖，覆盖 happy path 与错误路径。
"""

import hashlib
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.snapshots as snap_mod
from app import snapshot_service
from app.models import Base, Chapter, ChapterSnapshot, Novel, User
from app.snapshot_service import create_snapshot, diff_html, restore_snapshot


# ---------- 内存 SQLite fixture：每个测试独立 schema + 一个用户/作品/章节 ----------

@pytest_asyncio.fixture
async def db_chapter():
    """返回 (session, chapter)；每个测试独立引擎与表，测试结束自动释放。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        user = User(username="u", password_hash="x", role="author")
        s.add(user)
        await s.flush()
        novel = Novel(user_id=user.id, title="N", author="a", description="", genre="")
        s.add(novel)
        await s.flush()
        chapter = Chapter(novel_id=novel.id, title="ch1", content="<p>hi</p>", word_count=2, sort_order=0)
        s.add(chapter)
        await s.commit()
        await s.refresh(chapter)
        yield s, chapter


# ---------- service 层 ----------

@pytest.mark.asyncio
async def test_create_snapshot_inserts_and_returns(db_chapter):
    db, ch = db_chapter
    snap = await create_snapshot(db, ch, "manual", label="v1")
    assert snap is not None
    assert snap.trigger == "manual"
    assert snap.label == "v1"
    assert snap.content_hash == hashlib.sha256(b"<p>hi</p>").hexdigest()
    assert snap.word_count == 2
    assert snap.content == "<p>hi</p>"


@pytest.mark.asyncio
async def test_create_snapshot_dedupes_same_hash(db_chapter):
    db, ch = db_chapter
    snap1 = await create_snapshot(db, ch, "manual", label="first")
    # 同内容手动再建 → 应直接返回已有快照，不 INSERT
    snap2 = await create_snapshot(db, ch, "manual", label="second")
    assert snap2.id == snap1.id
    assert snap2.label == "first"  # 标签未变（旧记录胜出）


@pytest.mark.asyncio
async def test_create_snapshot_auto_throttled_in_window(db_chapter):
    """auto 触发在节流窗口内且 hash 不同 → 返回 None（节流命中）。"""
    db, ch = db_chapter
    ch.content = "<p>first</p>"
    ch.word_count = 1
    await db.commit()
    snap1 = await create_snapshot(db, ch, "auto")
    assert snap1 is not None
    # 改为不同内容（避免去重先行命中），再触发 auto
    ch.content = "<p>second</p>"
    ch.word_count = 1
    await db.commit()
    snap2 = await create_snapshot(db, ch, "auto")
    assert snap2 is None  # 距上次 auto 不足 600s


@pytest.mark.asyncio
async def test_create_snapshot_auto_creates_after_window(db_chapter, monkeypatch):
    """auto 触发越过节流窗口 → 正常创建。"""
    db, ch = db_chapter
    ch.content = "<p>first</p>"
    ch.word_count = 1
    await db.commit()
    snap1 = await create_snapshot(db, ch, "auto")
    assert snap1 is not None
    # 假装"现在"已经在 last_auto 之后 700s。snapshot_service 用
    # `from .models import utcnow` 把对象绑定到自己的模块级名字，所以必须
    # patch snapshot_service 模块级的 utcnow 名字才能影响服务内的引用。
    # future 必须是 aware datetime（utcnow 原型带 tzinfo），DB 读出的
    # created_at 是 naive，需要手动补 UTC 标记才能与阈值比较。
    future = (snap1.created_at + timedelta(seconds=700)).replace(tzinfo=timezone.utc)
    monkeypatch.setattr(snapshot_service, "utcnow", lambda: future)
    ch.content = "<p>second</p>"
    ch.word_count = 1
    await db.commit()
    snap2 = await create_snapshot(db, ch, "auto")
    assert snap2 is not None
    assert snap2.content_hash != snap1.content_hash
    assert snap2.trigger == "auto"


@pytest.mark.asyncio
async def test_restore_creates_pre_rollback_and_overwrites(db_chapter):
    """restore：先存当前内容为 pre_rollback，再把目标内容写回章节。"""
    db, ch = db_chapter
    ch.content = "<p>old</p>"
    ch.word_count = 1
    await db.commit()
    snap_a = await create_snapshot(db, ch, "manual", label="A")
    # 改章节
    ch.content = "<p>new</p>"
    ch.word_count = 1
    await db.commit()
    pre, restored = await restore_snapshot(db, ch, snap_a.id)
    assert pre.trigger == "pre_rollback"
    assert pre.content == "<p>new</p>"  # pre 存的是改后内容
    assert ch.content == "<p>old</p>"  # chapter 已写回
    assert restored.id == snap_a.id


# ---------- diff 纯函数 ----------

def test_diff_html_identical_text_has_no_changes():
    html = diff_html("line one\nline two", "line one\nline two")
    assert "diff_add" not in html
    assert "diff_chg" not in html
    assert "diff_sub" not in html
    # 标题行至少存在
    assert "<table" in html


def test_diff_html_different_text_shows_changes():
    html = diff_html("line one\nline two", "line one\nline TWO")
    # 改动行应有 diff_chg 或 diff_sub+diff_add 之一
    assert "diff_chg" in html or ("diff_sub" in html and "diff_add" in html)


# ---------- 路由层（monkeypatch 隔离 db） ----------

@pytest.mark.asyncio
async def test_post_snapshot_calls_create_with_manual(monkeypatch):
    """POST /snapshots：调 service.create_snapshot(trigger="manual", label=...) 并返回详情。"""
    called = {}

    async def fake_create(db, chapter, trigger, label=""):
        called["trigger"] = trigger
        called["label"] = label
        return SimpleNamespace(
            id=1,
            chapter_id=chapter.id,
            word_count=0,
            content_hash="x",
            label=label,
            trigger=trigger,
            created_at=None,
            content="<p>x</p>",
            content_text="x",
        )

    monkeypatch.setattr(snap_mod, "create_snapshot", fake_create)

    data = snap_mod.CreateSnapshotIn(label="hello")
    fake_chapter = SimpleNamespace(id=1)
    result = await snap_mod.create_snapshot_endpoint(data=data, chapter=fake_chapter, db=None)

    assert called == {"trigger": "manual", "label": "hello"}
    assert result["label"] == "hello"
    assert result["content"] == "<p>x</p>"


@pytest.mark.asyncio
async def test_list_snapshots_filters_by_trigger(monkeypatch):
    """?trigger=manual 只返回 manual 快照；非法 trigger → 400。"""
    rows = [
        SimpleNamespace(
            id=1, chapter_id=99, word_count=10, content_hash="h1", label="", trigger="manual", created_at=None
        )
    ]

    captured_filter = {}

    class FakeResult:
        def scalars(self_inner):
            return SimpleNamespace(all=lambda: rows)

    class FakeDB:
        async def execute(self, query):
            # 简易：从 query.where 子句中抓 trigger 条件（此处仅作存在性记录）
            captured_filter["called"] = True
            return FakeResult()

    fake_chapter = SimpleNamespace(id=99)
    out = await snap_mod.list_snapshots(trigger="manual", chapter=fake_chapter, db=FakeDB())
    assert captured_filter["called"] is True
    assert len(out) == 1
    assert out[0]["trigger"] == "manual"

    # 非法 trigger
    with pytest.raises(HTTPException) as exc:
        await snap_mod.list_snapshots(trigger="bogus", chapter=fake_chapter, db=FakeDB())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_post_restore_returns_pre_rollback_id(monkeypatch):
    """POST /{sid}/restore：返回 ok + pre_rollback_id + restored_snapshot 详情。"""
    fake_pre = SimpleNamespace(id=42)
    fake_restored = SimpleNamespace(
        id=1, chapter_id=1, word_count=10, content_hash="h", label="A", trigger="manual",
        created_at=None, content="<p>old</p>", content_text="old",
    )

    async def fake_restore(db, chapter, sid):
        return fake_pre, fake_restored

    monkeypatch.setattr(snap_mod, "restore_snapshot", fake_restore)

    fake_chapter = SimpleNamespace(id=1)
    result = await snap_mod.restore_snapshot_endpoint(sid=1, chapter=fake_chapter, db=None)
    assert result["ok"] is True
    assert result["pre_rollback_id"] == 42
    assert result["restored_snapshot"]["id"] == 1


@pytest.mark.asyncio
async def test_diff_route_returns_html_for_two_snapshots(monkeypatch):
    """GET /{a}/diff/{b}：取两快照的 content_text 调 diff_html，返回 {html: ...}。"""
    snap_a = SimpleNamespace(content_text="alpha\nbeta", chapter_id=1)
    snap_b = SimpleNamespace(content_text="alpha\ngamma", chapter_id=1)

    class FakeDB:
        async def get(self, _model, id):
            return snap_a if id == 1 else snap_b

    fake_chapter = SimpleNamespace(id=1)
    result = await snap_mod.diff_snapshots(a=1, b="2", chapter=fake_chapter, db=FakeDB())
    assert "html" in result
    assert "diff_chg" in result["html"] or "diff_sub" in result["html"]


@pytest.mark.asyncio
async def test_diff_route_404_on_missing_snapshot():
    """GET /{a}/diff/{b} 任一快照不存在 → 404。"""
    class FakeDB:
        async def get(self, _model, id):
            return None

    fake_chapter = SimpleNamespace(id=1)
    with pytest.raises(HTTPException) as exc:
        await snap_mod.diff_snapshots(a=1, b="2", chapter=fake_chapter, db=FakeDB())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_diff_route_with_current_keyword_uses_chapter_content():
    """b='current' 时不查 b 快照，直接用 chapter.content 的纯文本与 a 对比。
    这是给前端「与当前对比」按钮用的快捷端点，避免为对比临时建快照的副作用。
    """
    snap_a = SimpleNamespace(content_text="alpha", chapter_id=1)
    get_called_ids: list[int] = []

    class FakeDB:
        async def get(self, _model, id):
            get_called_ids.append(id)
            return snap_a  # 只为 a 返回；b="current" 时不应再查

    fake_chapter = SimpleNamespace(id=1, content="<p>beta</p>")
    result = await snap_mod.diff_snapshots(a=1, b="current", chapter=fake_chapter, db=FakeDB())

    # 只查了一次（a=1），没查 b
    assert get_called_ids == [1]
    assert "html" in result
    # alpha vs beta 至少有一处不同
    assert "diff_chg" in result["html"] or "diff_sub" in result["html"]


@pytest.mark.asyncio
async def test_diff_route_400_on_invalid_b():
    """b 既不是数字也不是 'current' → 400。"""
    class FakeDB:
        async def get(self, _model, id):
            return SimpleNamespace(content_text="x", chapter_id=1)

    fake_chapter = SimpleNamespace(id=1, content="<p>x</p>")
    with pytest.raises(HTTPException) as exc:
        await snap_mod.diff_snapshots(a=1, b="bogus", chapter=fake_chapter, db=FakeDB())
    assert exc.value.status_code == 400
