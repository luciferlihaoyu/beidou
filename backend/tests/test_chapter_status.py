"""章节状态 + 标签测试：覆盖 service/模型默认值、PUT 字段更新、列表过滤、越权。
service 层用真实内存 SQLite；router 层用真实路由函数 + 直连 db（与 test_snapshots.py 同模式）。
"""

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.chapters as ch_mod
from app.models import Base, Chapter, Novel, User


@pytest_asyncio.fixture
async def db_with_chapters():
    """返回 (session, novel)：含 3 章，分别 status=writing/draft/done，tags 各异。
    三个 tag 场景专门设计：精确匹配、子串避中、并集命中。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 模型已含 status/tags 列，create_all 已建好——无需手工 ALTER
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        u = User(username="u", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        chapters = [
            Chapter(
                novel_id=n.id,
                title="c1",
                content="<p>a</p>",
                word_count=1,
                sort_order=0,
                status="writing",
                tags=json.dumps(["伏笔", "日常"], ensure_ascii=False),
            ),
            Chapter(
                novel_id=n.id,
                title="c2",
                content="<p>b</p>",
                word_count=1,
                sort_order=1,
                status="draft",
                tags=json.dumps(["高潮"], ensure_ascii=False),
            ),
            Chapter(
                novel_id=n.id,
                title="c3",
                content="<p>c</p>",
                word_count=1,
                sort_order=2,
                status="done",
                tags=json.dumps(["伏笔", "高潮"], ensure_ascii=False),
            ),
        ]
        for c in chapters:
            s.add(c)
        await s.commit()
        for c in chapters:
            await s.refresh(c)
        yield s, n, chapters


# ---------- 模型默认值 ----------


@pytest.mark.asyncio
async def test_chapter_default_status_draft_empty_tags():
    """新建章节不显式传 status/tags 时，DB 默认值为 draft / []。"""
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
        c = Chapter(novel_id=n.id, title="c", content="<p>x</p>", word_count=1, sort_order=0)
        s.add(c)
        await s.commit()
        await s.refresh(c)
        assert c.status == "draft"
        assert c.tags == "[]"


@pytest.mark.asyncio
async def test_ensure_column_is_idempotent(tmp_path):
    """db._ensure_column 单元测试：模拟存量表缺列时补列，重复调用不抛错。
    这是 init_db 迁移逻辑的核心；本测试用真实 AsyncConnection + 临时文件 SQLite。
    """
    from sqlalchemy import text
    from app.db import _ensure_column

    db_file = tmp_path / "legacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 模拟旧版本：先删 status/tags 两列（SQLite ALTER TABLE DROP COLUMN 3.35+ 支持）
        await conn.execute(text("ALTER TABLE chapters DROP COLUMN status"))
        await conn.execute(text("ALTER TABLE chapters DROP COLUMN tags"))
        # 验证列已不存在
        cols = (await conn.execute(text("PRAGMA table_info(chapters)"))).fetchall()
        col_names = [r[1] for r in cols]
        assert "status" not in col_names
        assert "tags" not in col_names

    # 跑 _ensure_column 补列
    async with engine.begin() as conn:
        await _ensure_column(conn, "chapters", "status", "TEXT NOT NULL DEFAULT 'draft'")
        await _ensure_column(conn, "chapters", "tags", "TEXT NOT NULL DEFAULT '[]'")

    async with engine.connect() as conn:
        cols = (await conn.execute(text("PRAGMA table_info(chapters)"))).fetchall()
        col_names = [r[1] for r in cols]
        assert "status" in col_names
        assert "tags" in col_names

    # 再跑一次 _ensure_column 应幂等（不抛错、不重复 ADD）
    async with engine.begin() as conn:
        await _ensure_column(conn, "chapters", "status", "TEXT NOT NULL DEFAULT 'draft'")
        await _ensure_column(conn, "chapters", "tags", "TEXT NOT NULL DEFAULT '[]'")


@pytest.mark.asyncio
async def test_out_deserializes_tags_json_to_list(db_with_chapters):
    """_out 把 chapter.tags 的 JSON 字符串还原为 list[str] 输出。"""
    db, novel, chapters = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._out(pairs[0][0], pairs[0][1])
    assert isinstance(out["tags"], list)
    assert out["tags"] == ["伏笔", "日常"]
    assert out["status"] == "writing"


# ---------- PUT 端点 ----------


@pytest.mark.asyncio
async def test_update_chapter_status_only(db_with_chapters):
    """PUT 只传 status → chapter.status 改了，tags 不变。"""
    db, novel, chapters = db_with_chapters
    chapter = chapters[0]
    data = ch_mod.ChapterUpdateIn(status="done")
    novel_ns = SimpleNamespace(id=novel.id)
    result = await ch_mod.update_chapter(chapter_id=chapter.id, data=data, novel=novel_ns, db=db)
    assert result["status"] == "done"
    assert result["tags"] == ["伏笔", "日常"]  # 不动


@pytest.mark.asyncio
async def test_update_chapter_tags_only(db_with_chapters):
    """PUT 只传 tags → chapter.tags 改了，status 不变。"""
    db, novel, chapters = db_with_chapters
    chapter = chapters[0]
    data = ch_mod.ChapterUpdateIn(tags=["伏笔", "新标签"])
    novel_ns = SimpleNamespace(id=novel.id)
    result = await ch_mod.update_chapter(chapter_id=chapter.id, data=data, novel=novel_ns, db=db)
    assert result["tags"] == ["伏笔", "新标签"]
    assert result["status"] == "writing"  # 不动
    # 写回 DB 后读出仍是 JSON 字符串且 ensure_ascii=False
    await db.refresh(chapter)
    assert "新标签" in chapter.tags  # 中文未被 \uXXXX 编码


@pytest.mark.asyncio
async def test_update_chapter_status_invalid_rejected_by_pydantic():
    """Pydantic pattern 校验：非法 status → ValidationError（FastAPI 转为 422）。"""
    with pytest.raises(ValidationError):
        ch_mod.ChapterUpdateIn(status="bogus")


@pytest.mark.asyncio
async def test_update_chapter_clear_tags_with_empty_list(db_with_chapters):
    """PUT tags=[] 显式清空（与 None 不改区分）。"""
    db, novel, chapters = db_with_chapters
    chapter = chapters[0]
    data = ch_mod.ChapterUpdateIn(tags=[])
    novel_ns = SimpleNamespace(id=novel.id)
    result = await ch_mod.update_chapter(chapter_id=chapter.id, data=data, novel=novel_ns, db=db)
    assert result["tags"] == []


@pytest.mark.asyncio
async def test_update_chapter_no_status_or_tags_unchanged(db_with_chapters):
    """PUT 既不传 status 也不传 tags → 全部不动。"""
    db, novel, chapters = db_with_chapters
    chapter = chapters[0]
    original_status = chapter.status
    original_tags = chapter.tags
    data = ch_mod.ChapterUpdateIn()  # 全部 None
    novel_ns = SimpleNamespace(id=novel.id)
    result = await ch_mod.update_chapter(chapter_id=chapter.id, data=data, novel=novel_ns, db=db)
    assert result["status"] == original_status
    assert result["tags"] == ["伏笔", "日常"]


# ---------- 列表过滤 ----------


# ---------- 列表过滤（纯函数 _filter_chapter_pairs）----------


@pytest.mark.asyncio
async def test_filter_chapter_pairs_by_status(db_with_chapters):
    """纯函数：status=writing 只返回 writing 章节。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status="writing", tag=None)
    assert [c.id for c, _ in out] == [1]


@pytest.mark.asyncio
async def test_filter_chapter_pairs_by_single_tag(db_with_chapters):
    """纯函数：tag=伏笔 只返回含「伏笔」标签的章节。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status=None, tag=["伏笔"])
    assert {c.id for c, _ in out} == {1, 3}


@pytest.mark.asyncio
async def test_filter_chapter_pairs_tag_substring_no_false_positive(db_with_chapters):
    """纯函数：tag=伏 不会误中「伏笔」——引号包裹确保精确。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status=None, tag=["伏"])
    assert out == []  # 数据库里没有「伏」这个 tag


@pytest.mark.asyncio
async def test_filter_chapter_pairs_multi_tag_union(db_with_chapters):
    """纯函数：多 tag 取并集（任一命中）。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status=None, tag=["伏笔", "高潮"])
    assert {c.id for c, _ in out} == {1, 2, 3}


@pytest.mark.asyncio
async def test_filter_chapter_pairs_status_plus_tag_combined(db_with_chapters):
    """纯函数：status + tag 组合（AND 关系）。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status="done", tag=["伏笔"])
    assert {c.id for c, _ in out} == {3}


@pytest.mark.asyncio
async def test_filter_chapter_pairs_no_filter_returns_all_in_display_order(db_with_chapters):
    """纯函数：无过滤时按原顺序返回全部。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status=None, tag=None)
    assert [c.id for c, _ in out] == [1, 2, 3]


@pytest.mark.asyncio
async def test_filter_chapter_pairs_empty_tag_list_returns_all(db_with_chapters):
    """纯函数：tag=[] 视为无过滤（不进入 if 分支）。"""
    db, novel, _ = db_with_chapters
    pairs = await ch_mod._ordered_with_numbers(novel, db)
    out = ch_mod._filter_chapter_pairs(pairs, status=None, tag=[])
    assert [c.id for c, _ in out] == [1, 2, 3]


# ---------- 端到端：FastAPI 真实路由 ----------


@pytest.mark.asyncio
async def test_list_endpoint_e2e_with_status_filter(tmp_path):
    """E2E：httpx + ASGITransport 跑真实 FastAPI 路由，验证 status Query 被 FastAPI 正确解析。
    覆盖 list_chapters → _filter_chapter_pairs → _out 全链路 + FastAPI Query 解析。
    """
    import json
    from httpx import ASGITransport, AsyncClient
    from app.deps import get_current_user, get_db

    # 临时文件 SQLite：建表 + 预置数据
    db_file = tmp_path / "e2e.db"
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
        s.add(Chapter(novel_id=n.id, title="c1", content="<p>a</p>", word_count=1, sort_order=0, status="writing", tags=json.dumps(["伏笔"], ensure_ascii=False)))
        s.add(Chapter(novel_id=n.id, title="c2", content="<p>b</p>", word_count=1, sort_order=1, status="draft", tags=json.dumps(["高潮"], ensure_ascii=False)))
        await s.commit()
        user_id = u.id
        novel_id = n.id

    # override deps 跳过 auth + 换临时 db
    from app.main import app

    class _AnonUser:
        id = user_id
        role = "author"

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser()

    async def fake_get_db():
        async with SL() as s:
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get(f"/api/novels/{novel_id}/chapters", params={"status": "writing"})
            assert r.status_code == 200, f"got {r.status_code}: {r.text}"
            data = r.json()
            assert len(data) == 1
            assert data[0]["title"] == "c1"
            assert data[0]["status"] == "writing"
            assert data[0]["tags"] == ["伏笔"]

            r = await c.get(f"/api/novels/{novel_id}/chapters", params={"tag": ["伏笔", "高潮"]})
            assert r.status_code == 200
            data = r.json()
            assert {d["title"] for d in data} == {"c1", "c2"}

            # 非法 status → 422（FastAPI Query pattern 校验）
            r = await c.get(f"/api/novels/{novel_id}/chapters", params={"status": "bogus"})
            assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()  # 关键：避免 aiosqlite 后台 worker 跨 loop 调度时崩
