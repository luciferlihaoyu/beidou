"""P0 修复后独立审查发现的四项残余缺陷回归（TDD 钉死）。

四项残余（详见任务说明）：
- 残余 1（Low→隐患）：restore_entry 恢复章节后未重建 FTS 索引 → 恢复回来的章节全站搜不到。
- 残余 2（Low）：restore 用 payload 里的 volume_id 直接建章；分卷已删则留悬挂引用（本仓 FK 关）
  或抛 IntegrityError → 500（FK 开的部署）。修法：恢复前校验分卷是否存在（不在则置 None）
  + commit 外围捕获 IntegrityError → 回滚 → 400。
- 残余 3（Medium）：admin 代恢复是死代码——:136 的 `entry.user_id != user.id` 先把 admin 挡成 404，
  :141 的 admin 旁路永不可达。真实归档流一律以「小说属主」为 entry.user_id，所以 admin 恒被 404。
  修法：:136 放宽为 `(entry.user_id != user.id and user.role != "admin")`，与 get_owned_novel 口径一致。
- 残余 4（Medium）：search_library_fts 在 novel_id=None 时先全库取相关度前 N（LIMIT）再由路由层按
  用户过滤——多用户下常见词的前 N 可能全是他人条目，本人条目被挤出 top N → 用户搜不到自己的东西。
  修法：把「公共库 + 本人小说集合」的 scope 下推进 SQL（expanding bindparam，禁止 f-string 拼 id），
  路由层兜底过滤保留（纵深防御）。

测试范式沿用 tests/test_recycle_roundtrip.py、tests/test_library_search_scope.py：
临时 SQLite（tmp_path 文件库）+ dependency_overrides[get_db]/[get_current_user] 注入 _AnonUser 替身
+ httpx.ASGITransport（raise_app_exceptions=False，让未捕获异常落成 500，与生产一致）。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_restore_residuals.py -q
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.exc import StaleDataError

import app.routers.chapters as ch_mod
from app.deps import get_current_user, get_db
from app.main import app
from app.models import Base, Chapter, LibraryItem, Novel, RecycleBin, User, Volume
from app.routers.recycle import archive_to_recycle
from app.search_fts import ensure_fts, search_fts, search_library_fts, sync_chapter, sync_library_item


class _AnonUser:
    """E2E 依赖覆盖用的轻量用户替身：restore/search 只需 id / role。"""

    def __init__(self, user_id: int, role: str = "author"):
        self.id = user_id
        self.role = role


async def _make_env(tmp_path, name: str):
    """建临时文件库（含 FTS5 虚拟表），返回 (engine, SL)。"""
    db_file = tmp_path / f"{name}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_fts(conn)  # chapter_fts + library_items_fts
    SL = async_sessionmaker(engine, expire_on_commit=False)
    return engine, SL


# 各测试独有的检索词（纯中文短语，FTS 走 phrase query；词面互不交叉）。
# ⚠ 分词硬约束：unicode61 把连续 CJK 串当单 token，检索词单独成句才稳定命中。
RESTORE_MARK = "紫霄神雷诀"        # 残余 1：恢复后应能被 FTS 搜到
ADMIN_MARK = "admin代恢复验证章"    # 残余 3：admin 走真实路径代恢复


# ======================================================================
# 残余 1：恢复章节后 FTS 索引未重建 → 恢复回来的章节搜不到
# ======================================================================


@pytest.mark.asyncio
async def test_restore_chapter_rebuilds_fts_index(tmp_path):
    """真实删章路径归档 → 删章清 FTS → HTTP 恢复 → 全站 FTS 应重新搜到该章。

    修复前：restore_entry 只 db.add(Chapter) + commit，不重建 FTS，
    恢复回来的章节在 chapter_fts 里没有行 → search_fts 搜不到（RED）。
    修复后：restore 末尾调 sync_chapter 重建索引 → 搜得到（GREEN）。
    """
    engine, SL = await _make_env(tmp_path, "residual1")

    async with SL() as s:
        u = User(username="r1", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N1", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        ch = Chapter(
            novel_id=n.id,
            title="第一章 雷诀",
            content=f"<p>{RESTORE_MARK}：恢复后仍可全文检索。</p>",
            word_count=10,
            sort_order=0,
            status="draft",
            tags="[]",
        )
        s.add(ch)
        await s.commit()
        await s.refresh(n)
        await s.refresh(ch)
        novel_id, user_id = n.id, u.id
        # 建章时同步进 FTS（模拟正常写入路径）
        await sync_chapter(s, ch.id)

        # sanity：删除前 FTS 能搜到
        before = await search_fts(s, novel_id, RESTORE_MARK)
        assert any(RESTORE_MARK in (r["title"] or "") or True for r in before) and len(before) >= 1, (
            f"删除前 FTS 应能搜到该章，实际 {before}"
        )

        # 真实删除路径：归档 + 清 FTS + 删行
        await ch_mod._delete_one_chapter(s, n, ch)
        await s.commit()

        # sanity：删除后 FTS 搜不到（remove_chapter 生效）
        after_del = await search_fts(s, novel_id, RESTORE_MARK)
        assert after_del == [], f"删除后 FTS 应搜不到，实际 {after_del}"

        entry_id = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "chapter"))
        ).scalars().first().id

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
            assert r.status_code == 200, f"恢复应 200，实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()

    # 独立 session 校验：章节回来了，且 FTS 重新能搜到
    async with SL() as s:
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == novel_id))
        ).scalars().all()
        assert len(chapters) == 1, f"章节应被恢复，实际 {len(chapters)} 条"
        hits = await search_fts(s, novel_id, RESTORE_MARK)
        hit_ids = {h["chapter_id"] for h in hits}
        assert chapters[0].id in hit_ids, (
            f"恢复后 FTS 应重新索引到该章（残余 1），实际命中 {hit_ids}，章节 id={chapters[0].id}"
        )

    await engine.dispose()


# ======================================================================
# 残余 2：恢复时 volume_id 指向已删分卷 → 悬挂引用（FK 关）/ 500（FK 开）
# ======================================================================


@pytest.mark.asyncio
async def test_restore_chapter_with_deleted_volume_nullifies(tmp_path):
    """章节归档后其分卷被删 → 恢复应成功且 volume_id 置 None（不留悬挂引用）。

    修复前：restore 用 payload 的 volume_id 直接建章；本仓 FK 关，会静默留下
    指向已删分卷的悬挂 volume_id（数据不一致；FK 开的部署则 500）→ 断言 None 失败（RED）。
    修复后：恢复前校验分卷不存在 → volume_id=None（GREEN）。
    """
    engine, SL = await _make_env(tmp_path, "residual2a")

    async with SL() as s:
        u = User(username="r2", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N2", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        vol = Volume(novel_id=n.id, title="第一卷", sort_order=0)
        s.add(vol)
        await s.flush()
        ch = Chapter(
            novel_id=n.id,
            volume_id=vol.id,
            title="卷内章",
            content="<p>正文</p>",
            word_count=2,
            sort_order=0,
            status="draft",
            tags="[]",
        )
        s.add(ch)
        await s.commit()
        await s.refresh(n)
        await s.refresh(ch)
        await s.refresh(vol)
        novel_id, user_id, volume_id = n.id, u.id, vol.id

        # 真实删除路径归档（payload 带 volume_id）
        await ch_mod._delete_one_chapter(s, n, ch)
        await s.commit()
        # 删掉分卷，制造「volume_id 指向已删分卷」
        await s.delete(vol)
        await s.commit()
        assert await s.get(Volume, volume_id) is None, "前置：分卷应已删除"
        entry_id = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "chapter"))
        ).scalars().first().id

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
            assert r.status_code == 200, f"分卷已删时恢复仍应 200（不得 500），实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()

    async with SL() as s:
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == novel_id))
        ).scalars().all()
        assert len(chapters) == 1, f"章节应被恢复，实际 {len(chapters)} 条"
        assert chapters[0].volume_id is None, (
            f"分卷已删，恢复后 volume_id 必须置 None（残余 2），实际悬挂为 {chapters[0].volume_id}"
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_restore_integrity_error_returns_400_not_500(tmp_path):
    """commit 抛 IntegrityError 时应回滚并返回 400（而非裸 500）。

    这是残余 2 的兜底：分卷校验之外的任何完整性错误（FK 开的部署、并发删除等）
    都应被 `except IntegrityError` 收敛成 400。用打桩 session.commit 抛 IntegrityError
    模拟数据库完整性失败（这是测试错误处理分支的标准手法）。

    修复前：commit 外无 try/except → IntegrityError 逃逸 → 500（RED）。
    修复后：捕获 → rollback → HTTPException(400)（GREEN）。
    """
    engine, SL = await _make_env(tmp_path, "residual2b")

    async with SL() as s:
        u = User(username="r2b", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="N2b", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        await archive_to_recycle(
            s,
            user=u,
            novel_id=n.id,
            kind="chapter",
            name="完整性章",
            payload={
                "title": "完整性章",
                "content": "<p>x</p>",
                "word_count": 1,
                "sort_order": 0,
                "volume_id": None,
                "status": "draft",
                "tags": "[]",
            },
        )
        await s.commit()
        user_id = u.id
        entry_id = (await s.execute(select(RecycleBin))).scalars().first().id

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(user_id, "author")

    async def fake_get_db():
        async with SL() as s:
            # 打桩：让 commit 抛 IntegrityError，模拟数据库完整性约束失败
            async def boom_commit():
                raise IntegrityError(
                    "INSERT INTO chapters ...", {}, Exception("FOREIGN KEY constraint failed")
                )

            s.commit = boom_commit
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            r = await c.post(f"/api/recycle/{entry_id}/restore")
            assert r.status_code == 400, (
                f"IntegrityError 应收敛为 400（残余 2 兜底），实际 {r.status_code}: {r.text}"
            )
            assert "关联数据已不存在" in r.text, f"400 文案应说明关联数据缺失，实际：{r.text}"
    finally:
        app.dependency_overrides.clear()

    # 回滚后：废纸篓条目应仍在（未被删），章节未落库
    async with SL() as s:
        remaining = (await s.execute(select(RecycleBin))).scalars().all()
        assert len(remaining) == 1, f"回滚后废纸篓条目应保留，实际 {len(remaining)} 条"
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == remaining[0].novel_id))
        ).scalars().all()
        assert len(chapters) == 0, f"回滚后不应留下章节，实际 {len(chapters)} 条"

    await engine.dispose()


# ======================================================================
# 对抗式审查收尾项 L1：volume_id 只查存在不查归属 → 脏 payload 可跨小说挂载
# ======================================================================


@pytest.mark.asyncio
async def test_restore_chapter_with_foreign_novel_volume_nullifies(tmp_path):
    """收尾 L1：脏 payload 塞「另一本小说」的分卷 id → 恢复后 volume_id 必须置 None。

    分卷预校验不仅要确认「存在」，还要确认「属于本小说」。否则硬删/并发下的脏 payload
    能把章节挂到别本小说的分卷上（跨小说数据错乱）。
    修复前：db.get(Volume, id) 返回他书分卷（非 None）→ volume_id 原样保留 → 章节挂到
    他书分卷（RED）；修复后：novel_id 不匹配 → 置 None（GREEN）。
    """
    engine, SL = await _make_env(tmp_path, "residual_l1")

    async with SL() as s:
        u = User(username="l1", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        na = Novel(user_id=u.id, title="A之书", author="a", description="", genre="")
        nb = Novel(user_id=u.id, title="B之书", author="a", description="", genre="")
        s.add_all([na, nb])
        await s.flush()
        vol_b = Volume(novel_id=nb.id, title="B书第一卷", sort_order=0)  # 属于 B 书
        s.add(vol_b)
        await s.flush()
        vol_b_id, novel_a_id, user_id = vol_b.id, na.id, u.id
        # 脏 payload：条目归属 A 书，但 volume_id 指向 B 书的分卷
        await archive_to_recycle(
            s,
            user=u,
            novel_id=novel_a_id,
            kind="chapter",
            name="脏分卷章",
            payload={
                "title": "脏分卷章",
                "content": "<p>跨小说脏数据</p>",
                "word_count": 5,
                "sort_order": 0,
                "volume_id": vol_b_id,  # ← 关键：他书分卷
                "status": "draft",
                "tags": "[]",
            },
        )
        await s.commit()
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
            assert r.status_code == 200, f"恢复应 200，实际 {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()

    async with SL() as s:
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == novel_a_id))
        ).scalars().all()
        assert len(chapters) == 1, f"章节应恢复到 A 书下，实际 {len(chapters)} 条"
        assert chapters[0].volume_id is None, (
            f"他书分卷不得沿用，volume_id 应置 None（L1），实际 {chapters[0].volume_id}（他书分卷 {vol_b_id}）"
        )

    await engine.dispose()


# ======================================================================
# 对抗式审查收尾项 L4：并发双恢复 StaleDataError 裸 500 → 应收敛为 409
# ======================================================================


@pytest.mark.asyncio
async def test_restore_stale_data_error_returns_409(tmp_path):
    """收尾 L4：并发双恢复时 SQLAlchemy 抛 StaleDataError → 收敛为 409（而非裸 500）。

    两人同时恢复同一条废纸篓：后提交者发现条目已被前者删除/改动，ORM 乐观锁抛
    StaleDataError（不是 IntegrityError，二者不同继承链）。修复前只兜 IntegrityError
    → StaleDataError 逃逸 → 500（RED）；修复后 except StaleDataError → rollback →
    409 Conflict（GREEN，语义比 400 更贴切：资源状态冲突，非请求本身错误）。
    用打桩 session.commit 抛 StaleDataError 验证该错误处理分支。
    """
    engine, SL = await _make_env(tmp_path, "residual_l4")

    async with SL() as s:
        u = User(username="l4", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n = Novel(user_id=u.id, title="NL4", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        await archive_to_recycle(
            s,
            user=u,
            novel_id=n.id,
            kind="chapter",
            name="并发章",
            payload={
                "title": "并发章",
                "content": "<p>x</p>",
                "word_count": 1,
                "sort_order": 0,
                "volume_id": None,
                "status": "draft",
                "tags": "[]",
            },
        )
        await s.commit()
        user_id = u.id
        entry_id = (await s.execute(select(RecycleBin))).scalars().first().id

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(user_id, "author")

    async def fake_get_db():
        async with SL() as s:
            # 打桩：让 commit 抛 StaleDataError，模拟并发恢复的乐观锁失败
            async def boom_commit():
                raise StaleDataError(
                    "UPDATE statement on table expected 1 row(s); 0 were matched."
                )

            s.commit = boom_commit
            yield s

    app.dependency_overrides[get_current_user] = fake_current_user
    app.dependency_overrides[get_db] = fake_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            r = await c.post(f"/api/recycle/{entry_id}/restore")
            assert r.status_code == 409, (
                f"StaleDataError 应收敛为 409（L4），实际 {r.status_code}: {r.text}"
            )
            assert "已被恢复或删除" in r.text, f"409 文案应说明并发冲突，实际：{r.text}"
    finally:
        app.dependency_overrides.clear()

    # 回滚后：废纸篓条目应仍在（未被删），章节未落库
    async with SL() as s:
        remaining = (await s.execute(select(RecycleBin))).scalars().all()
        assert len(remaining) == 1, f"回滚后废纸篓条目应保留，实际 {len(remaining)} 条"
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == remaining[0].novel_id))
        ).scalars().all()
        assert len(chapters) == 0, f"回滚后不应留下章节，实际 {len(chapters)} 条"

    await engine.dispose()


# ======================================================================
# 残余 3：admin 代恢复是死代码——真实路径下 admin 恒被 :136 挡成 404
# ======================================================================


@pytest.mark.asyncio
async def test_admin_restores_entry_archived_by_owner(tmp_path):
    """真实路径：属主 A 删章归档（entry.user_id=A）→ admin 走 HTTP 恢复应 200 且章节回来。

    不再人工构造 admin 条目（那是假达标）。这里 entry.user_id 恒为属主 A，
    修复前 :136 的 `entry.user_id != user.id`（A != admin）先把 admin 挡成 404（RED）；
    修复后 :136 放宽 admin 旁路 → 200，章节在 A 的小说下重建（GREEN）。
    """
    engine, SL = await _make_env(tmp_path, "residual3")

    async with SL() as s:
        owner = User(username="owner3", password_hash="x", role="author")
        admin = User(username="admin3", password_hash="x", role="admin")
        s.add_all([owner, admin])
        await s.flush()
        n = Novel(user_id=owner.id, title="A之书", author="a", description="", genre="")
        s.add(n)
        await s.flush()
        ch = Chapter(
            novel_id=n.id,
            title="待删章",
            content=f"<p>{ADMIN_MARK}：由属主归档，admin 代恢复。</p>",
            word_count=12,
            sort_order=0,
            status="draft",
            tags="[]",
        )
        s.add(ch)
        await s.commit()
        await s.refresh(n)
        await s.refresh(ch)
        novel_id, admin_id, owner_id = n.id, admin.id, owner.id

        # 真实删除路径：archive_to_recycle 以「小说属主」为 user → entry.user_id = owner_id
        await ch_mod._delete_one_chapter(s, n, ch)
        await s.commit()

        entry = (
            await s.execute(select(RecycleBin).where(RecycleBin.kind == "chapter"))
        ).scalars().first()
        entry_id = entry.id
        # 前置断言：归档条目的 user_id 是属主，不是 admin（这正是 admin 代恢复被挡的病根）
        assert entry.user_id == owner_id, f"归档 entry.user_id 应为属主 {owner_id}，实际 {entry.user_id}"
        assert entry.user_id != admin_id

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
            assert r.status_code == 200, (
                f"admin 代恢复他人归档条目应 200（残余 3），实际 {r.status_code}: {r.text}"
            )
            assert r.json()["ok"] is True
    finally:
        app.dependency_overrides.clear()

    async with SL() as s:
        chapters = (
            await s.execute(select(Chapter).where(Chapter.novel_id == novel_id))
        ).scalars().all()
        assert len(chapters) == 1, f"admin 代恢复后章节应重建，实际 {len(chapters)} 条"
        assert ADMIN_MARK in (chapters[0].content or ""), "恢复的章节正文应含原内容"
        remaining = (await s.execute(select(RecycleBin))).scalars().all()
        assert len(remaining) == 0, "恢复后废纸篓条目应被删除"

    await engine.dispose()


# ======================================================================
# 残余 4：搜索「先截断后过滤」把本人合法结果挤出 top N
# ======================================================================


@pytest.mark.asyncio
async def test_own_item_not_pushed_out_by_many_foreign_hits(tmp_path):
    """端到端钉死：他人条目塞满相关度 top N 时，本人条目仍必须被搜到。

    构造：novel_A（他人）建 32 条「短小高相关」条目命中同一常见词，novel_B（本人）建 1 条
    「长文低相关」条目命中同词。FTS bm25 下 32 条 A 条目相关度全部高于 B 条目。
    B 不带 novel_id 搜索（默认 limit=30）：
      修复前：FTS 先全库取相关度前 30（全是 A）→ 路由层按用户过滤把 A 全丢 → B 拿到 []，
              自己的条目被挤出 top N（RED）。
      修复后：scope 下推进 SQL（只取「公共库 + B 的小说」）→ A 条目在 SQL 层就不参与 top N
              → B 的条目被返回（GREEN）。
    """
    engine, SL = await _make_env(tmp_path, "residual4")
    shared = "混检常见词"

    async with SL() as s:
        ua = User(username="r4a", password_hash="x", role="author")
        ub = User(username="r4b", password_hash="x", role="author")
        s.add_all([ua, ub])
        await s.flush()
        na = Novel(user_id=ua.id, title="A之书", author="a", description="", genre="")
        nb = Novel(user_id=ub.id, title="B之书", author="b", description="", genre="")
        s.add_all([na, nb])
        await s.flush()
        novel_a, novel_b, user_b = na.id, nb.id, ub.id

        # 32 条他人（novel_A）短小高相关条目 → bm25 全部排在 B 的长文之前
        pad = "无关填充内容" * 120
        for i in range(32):
            it = LibraryItem(novel_id=novel_a, title=f"A{i}", content=shared)
            s.add(it)
        # 1 条本人（novel_B）长文低相关条目
        own = LibraryItem(novel_id=novel_b, title="B自己的资料", content=f"{shared}，{pad}")
        s.add(own)
        await s.commit()
        own_id = own.id
        # 全部同步进 FTS
        items = (await s.execute(select(LibraryItem))).scalars().all()
        for it in items:
            await sync_library_item(s, it.id)

    async def fake_current_user(credentials=None, db=None):
        return _AnonUser(user_b, "author")

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
            r = await c.get("/api/library/search", params={"q": shared})
            assert r.status_code == 200, r.text
            ids = {row["id"] for row in r.json()}
            assert own_id in ids, (
                f"他人条目塞满 top N 时，本人条目仍须被搜到（残余 4）；实际返回 {len(ids)} 条且不含本人 id={own_id}"
            )
            # 纵深防御：他人小说 scope 的条目一条都不许出网
            assert not (ids - {own_id}), f"只应返回本人/公共条目，实际混入他人：{ids - {own_id}}"
    finally:
        app.dependency_overrides.clear()

    await engine.dispose()


@pytest.mark.asyncio
async def test_search_library_fts_scope_pushdown_and_empty_set(tmp_path):
    """FTS 层单元钉：scope 下推进 SQL；空集合语义 = 只搜公共库。

    - allowed_novel_ids={own} 时：即便他人条目相关度更高、limit 很小，也只返回
      「公共库 + own 集合」内的命中，他人 scope 在 SQL 层就被排除（不是靠路由层事后过滤）。
    - allowed_novel_ids=set() 时：只返回公共库条目。
    - novel_id 非 None 时：行为完全不变（本书 + 公共）。
    """
    engine, SL = await _make_env(tmp_path, "residual4b")
    term = "下推验证词"

    async with SL() as s:
        u = User(username="r4c", password_hash="x", role="author")
        s.add(u)
        await s.flush()
        n_own = Novel(user_id=u.id, title="own", author="a", description="", genre="")
        n_other = Novel(user_id=u.id, title="other", author="a", description="", genre="")
        s.add_all([n_own, n_other])
        await s.flush()
        pad = "填充" * 200
        # 3 条他人短小高相关 + 1 条本人长文低相关 + 1 条公共
        others = [LibraryItem(novel_id=n_other.id, title=f"o{i}", content=term) for i in range(3)]
        own = LibraryItem(novel_id=n_own.id, title="own_item", content=f"{term}，{pad}")
        pub = LibraryItem(novel_id=None, title="pub_item", content=f"{term}：公共")
        s.add_all(others + [own, pub])
        await s.commit()
        own_id, pub_id = own.id, pub.id
        other_ids = [o.id for o in others]
        for it in others + [own, pub]:
            await sync_library_item(s, it.id)

        # 1) allowed={n_own}，limit=2：他人 3 条相关度更高，若不下推则本人/公共被挤出
        scoped = await search_library_fts(s, term, novel_id=None, limit=2, allowed_novel_ids={n_own.id})
        scoped_ids = {r["id"] for r in scoped}
        assert own_id in scoped_ids, f"scope 下推后本人条目须在 top N 内（残余 4），实际 {scoped_ids}"
        assert not (scoped_ids & set(other_ids)), f"他人 scope 须在 SQL 层排除，实际混入 {scoped_ids & set(other_ids)}"

        # 2) 空集合：只搜公共库
        only_pub = await search_library_fts(s, term, novel_id=None, limit=10, allowed_novel_ids=set())
        only_pub_ids = {r["id"] for r in only_pub}
        assert only_pub_ids == {pub_id}, f"空集合应只返回公共库，实际 {only_pub_ids}（pub={pub_id}）"

        # 3) novel_id 非 None：行为完全不变（本书 + 公共），不受 allowed_novel_ids 影响
        by_novel = await search_library_fts(s, term, novel_id=n_own.id, limit=10)
        by_novel_ids = {r["id"] for r in by_novel}
        assert own_id in by_novel_ids and pub_id in by_novel_ids, f"带 novel_id 应含本书+公共，实际 {by_novel_ids}"
        assert not (by_novel_ids & set(other_ids)), f"带 novel_id 不应含他书，实际 {by_novel_ids}"

    await engine.dispose()
