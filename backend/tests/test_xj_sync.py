"""璇玑资料库双向同步「批D：数据面 + 入方向引擎」回归测试（r1+r2）。

- r1 数据面：IntegrationConfig 四项（xj_sync_enabled / xj_sync_folders /
  xj_sync_interval_min / xj_sync_last_at）+ LibraryItem 四项（xj_doc_id /
  xj_doc_updated_at / xj_local_dirty / xj_conflict）。夹具走
  ``Base.metadata.create_all``（新库口径），迁移存量库的口径另由
  ``db._ensure_column`` 八行负责（幂等自验见临时脚本，不入仓库）。
- r2 入方向引擎：``sync_from_xuanji`` —— getTree → 白名单文件夹过滤（硬排除
  「北斗小说资料」）→ 按 xj_doc_id 建本地映射 → 纯函数 ``_diff_in`` 分流
  （new / overwrite / protect / conflict）→ 落库镜像 + 镜像目录同名复用。

引擎语义（与计划 v2 + 碧霄 2026-10-14 裁决一致）：
  ① 璇玑 updatedAt 是变更锚，比较前统一归一化为字符串——前提是 ISO 8601
     形态（字典序 == 时间序）；纯数字按 epoch 毫秒折算（m3 防御）；
  ② 本地 dirty 且璇玑未变 → 保护不覆盖（to_protect；回写璇玑是出方向的事）；
  ③ 本地 dirty 且璇玑已变 → 只置 xj_conflict，两边内容与基线都不动，交人工裁；
  ④ 璇玑变且本地干净 → 只覆盖 content + 刷基线，**标题保留本地别名**；
  ⑤ 本地映射只认 xj_doc_id，不带 source 谓词（C1：条目经资料库路由保存会
     把 source 冲成 manual，带谓词会脱锚 → 重复镜像）；
  ⑥ 单篇 getDocument 失败 → 跳过该篇计入 failed，其余继续；getTree 失败上抛。
"""

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.integrations as integ_mod
import app.routers.library as library_mod
from app import xj_sync
from app.models import Base, IntegrationConfig, LibraryFolder, LibraryItem, User

# ---------- 共享 helper：内存库 + 璇玑树造数 ----------

#: 璇玑侧文件夹：两个白名单目录（设定集 / 素材库）+ 正向设定集落点（必须硬排除）
FOLDER_SPEC = {"id": 1, "name": "设定集", "parentId": None, "icon": None}
FOLDER_MATERIAL = {"id": 2, "name": "素材库", "parentId": None, "icon": None}
FOLDER_SPEC_SINK = {"id": 3, "name": "北斗小说资料", "parentId": None, "icon": None}

#: 璇玑文档 id → 全文（kb.getDocument 返回体）
DOC_FULL = {
    101: {"id": 101, "title": "世界观", "content": "灵气复苏，剑修昌盛。"},
    102: {"id": 102, "title": "人物卡", "content": "林青云：外门弟子。"},
    999: {"id": 999, "title": "北斗设定集", "content": "正向同步落点，绝不反向拉回。"},
}


@pytest_asyncio.fixture
async def sl():
    """独立内存库（create_all 直接带批D 新列）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def sl_fts():
    """内存库 + FTS5 虚拟表：走真实 ``search_fts.ensure_fts`` 建表口径（m1 用）。"""
    from app.search_fts import ensure_fts

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_fts(conn)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _to_iso(day: int) -> str:
    return f"2026-10-{day:02d}T10:00:00.000Z"


def _patch_xuanji_call(monkeypatch, *, documents=None, folders=None, docs=None, fail_ids=()):
    """Mock 璇玑读通道：getTree 返回预设树；getDocument 按 id 返回全文，fail_ids 内的抛异常。"""
    recorded: dict = {"calls": [], "get_document": []}
    tree = {
        "folders": list(folders if folders is not None else [FOLDER_SPEC, FOLDER_MATERIAL, FOLDER_SPEC_SINK]),
        "documents": list(documents or []),
    }
    fulls = dict(DOC_FULL if docs is None else docs)

    async def fake_call(config, procedure, input_data=None):
        recorded["calls"].append(procedure)
        if procedure == "kb.getTree":
            return tree
        if procedure == "kb.getDocument":
            doc_id = (input_data or {}).get("id")
            recorded["get_document"].append(doc_id)
            if doc_id in fail_ids:
                raise RuntimeError("璇玑单篇读取炸了")
            return fulls.get(doc_id)
        return None

    monkeypatch.setattr(integ_mod, "_xuanji_call", fake_call)
    return recorded


async def _make_config(sl: async_sessionmaker, **kw) -> IntegrationConfig:
    """造一份集成配置（复用同一测试用户，避免 username 唯一约束撞车）。"""
    async with sl() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        if user is None:
            user = User(username="xj-tester", password_hash="x")
            db.add(user)
            await db.flush()
        fields = {"xuanji_url": "https://xj.example", "xj_sync_folders": "设定集,素材库"}
        fields.update(kw)
        cfg = IntegrationConfig(user_id=user.id, **fields)
        db.add(cfg)
        await db.commit()
        return cfg


async def _make_item(sl: async_sessionmaker, **kw) -> LibraryItem:
    """造一条本地同步条目（默认 source=xuanji、干净、基线为 10-01）。"""
    fields = {
        "novel_id": None,
        "folder_id": None,
        "title": "世界观",
        "content": "本地旧正文",
        "tags": "璇玑同步",
        "source": "xuanji",
        "xj_doc_id": "101",
        "xj_doc_updated_at": _to_iso(1),
    }
    fields.update(kw)
    async with sl() as db:
        item = LibraryItem(**fields)
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item


async def _items(sl: async_sessionmaker) -> list[LibraryItem]:
    async with sl() as db:
        rows = (await db.execute(select(LibraryItem).order_by(LibraryItem.id))).scalars().all()
        return list(rows)


async def _folders(sl: async_sessionmaker) -> list[LibraryFolder]:
    async with sl() as db:
        rows = (await db.execute(select(LibraryFolder).order_by(LibraryFolder.id))).scalars().all()
        return list(rows)


# ---------- r1：数据面默认值 ----------


@pytest.mark.asyncio
async def test_r1_model_defaults(sl):
    """r1：两表八列齐备，默认值为 enabled=False / folders="" / interval=10 / last_at="" / 条目四列空。"""
    async with sl() as db:
        user = User(username="xj-defaults", password_hash="x")
        db.add(user)
        await db.flush()
        cfg = IntegrationConfig(user_id=user.id)
        item = LibraryItem(title="手工条目", source="manual")
        db.add_all([cfg, item])
        await db.commit()
        await db.refresh(cfg)
        await db.refresh(item)

    assert (cfg.xj_sync_enabled, cfg.xj_sync_folders, cfg.xj_sync_interval_min, cfg.xj_sync_last_at) == (
        False,
        "",
        10,
        "",
    )
    assert (item.xj_doc_id, item.xj_doc_updated_at, item.xj_local_dirty, item.xj_conflict) == ("", "", False, False)
    assert cfg.xj_sync_folders is not None and item.xj_doc_id is not None  # 非 NULL 列（迁移口径一致）


# ---------- r2①：新文档插入 + 镜像目录 ----------


@pytest.mark.asyncio
async def test_new_docs_inserted_and_mirror_folder_reused(sl, monkeypatch):
    """白名单内新文档 → 建镜像条目（公共库、source=xuanji、带 doc id 与基线）；同名目录复用不重建。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[
            {"id": 101, "title": "世界观", "folderId": 1, "format": "markdown", "updatedAt": _to_iso(1)},
            {"id": 102, "title": "人物卡", "folderId": 2, "format": "markdown", "updatedAt": _to_iso(2)},
            {"id": 999, "title": "北斗设定集", "folderId": 3, "format": "markdown", "updatedAt": _to_iso(3)},
        ],
    )
    cfg = await _make_config(sl)
    # 预置一个同名公共目录（素材库）：同步时必须复用，而不是再建一个
    async with sl() as db:
        existing = LibraryFolder(novel_id=None, parent_id=None, name="素材库")
        db.add(existing)
        await db.commit()
        existing_id = existing.id

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert out["added"] == 2
    assert out["updated"] == 0
    assert out["failed"] == 0
    assert [f["name"] for f in out["folders"]] == ["设定集", "素材库"]
    assert [f["docs"] for f in out["folders"]] == [1, 1]

    folders = await _folders(sl)
    assert len(folders) == 2, "素材库应复用、设定集应新建，不能多出目录"
    assert folders[-1].name == "设定集" and folders[-1].novel_id is None

    items = await _items(sl)
    assert [i.title for i in items] == ["世界观", "人物卡"]
    by_doc = {i.xj_doc_id: i for i in items}
    assert by_doc["101"].content == "灵气复苏，剑修昌盛。"
    assert by_doc["101"].xj_doc_updated_at == _to_iso(1)
    assert by_doc["102"].folder_id == existing_id  # 复用预置目录
    assert all(i.tags == "璇玑同步" for i in items)
    assert all(i.source == "xuanji" and i.novel_id is None for i in items)
    assert "999" not in by_doc, "北斗小说资料 目录内文档必须硬排除"

    # 二次运行：目录不重建、文档不再新增
    async with sl() as db:
        out2 = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)
    assert (out2["added"], out2["updated"], out2["skipped"]) == (0, 0, 2)
    assert len(await _folders(sl)) == 2

    async with sl() as db:
        assert (await db.get(IntegrationConfig, cfg.id)).xj_sync_last_at != "", "成功一轮应写 xj_sync_last_at"


# ---------- r2②：璇玑变了、本地干净 → 覆盖 ----------


@pytest.mark.asyncio
async def test_overwrite_when_xuanji_changed_and_local_clean(sl, monkeypatch):
    """璇玑 updatedAt > 基线且本地非 dirty → 覆盖 content 并刷新基线。

    标题**不覆盖**（碧霄 2026-10-14 口径）：本地标题=别名，与出方向「标题不回推」对称。
    """
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观 v2", "folderId": 1, "updatedAt": _to_iso(5)}],
        docs={101: {"id": 101, "title": "世界观 v2", "content": "璇玑新版正文"}},
    )
    cfg = await _make_config(sl)
    item = await _make_item(sl)  # 本地标题「世界观」

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert (out["added"], out["updated"], out["conflicts"], out["protected"]) == (0, 1, 0, 0)
    fresh = (await _items(sl))[0]
    assert fresh.id == item.id  # 原位更新，不新建
    assert fresh.title == "世界观", "标题是作者别名，入方向不得覆盖"
    assert fresh.content == "璇玑新版正文"
    assert fresh.xj_doc_updated_at == _to_iso(5)
    assert fresh.xj_local_dirty is False


# ---------- r2③：璇玑未变 → skipped ----------


@pytest.mark.asyncio
async def test_skipped_when_unchanged(sl, monkeypatch):
    """璇玑 updatedAt == 基线且本地干净 → skipped，内容一动不动。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
    )
    cfg = await _make_config(sl)
    await _make_item(sl)

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert (out["added"], out["updated"], out["conflicts"], out["protected"], out["skipped"]) == (0, 0, 0, 0, 1)
    item = (await _items(sl))[0]
    assert item.content == "本地旧正文"


# ---------- r2④：本地 dirty、璇玑未变 → 保护不覆盖 ----------


@pytest.mark.asyncio
async def test_dirty_local_protected_when_xuanji_unchanged(sl, monkeypatch):
    """本地已改（dirty）且璇玑未变 → 计入 protected，内容与 dirty 标记都不动（回写归批E）。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
    )
    cfg = await _make_config(sl)
    await _make_item(sl, content="作者本地改过的正文", xj_local_dirty=True)

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert (out["added"], out["updated"], out["conflicts"], out["protected"], out["skipped"], out["failed"]) == (
        0,
        0,
        0,
        1,
        0,
        0,
    )
    item = (await _items(sl))[0]
    assert item.content == "作者本地改过的正文"
    assert item.xj_local_dirty is True
    assert item.xj_conflict is False


# ---------- r2⑤：两边都改 → conflict ----------


@pytest.mark.asyncio
async def test_conflict_flags_without_touching_content(sl, monkeypatch):
    """本地 dirty 且璇玑已变 → 置 xj_conflict=True；两边内容与基线都不动，交人工裁。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(6)}],
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑新版"}},
    )
    cfg = await _make_config(sl)
    await _make_item(sl, content="作者本地改过的正文", xj_local_dirty=True)

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert (out["updated"], out["conflicts"], out["protected"], out["skipped"]) == (0, 1, 0, 0)
    item = (await _items(sl))[0]
    assert item.xj_conflict is True
    assert item.content == "作者本地改过的正文", "冲突时本地内容不得被覆盖"
    assert item.xj_doc_updated_at == _to_iso(1), "冲突时基线不得前移（否则丢失待裁信息）"
    assert item.xj_local_dirty is True


# ---------- r2⑥：硬排除「北斗小说资料」 ----------


@pytest.mark.asyncio
async def test_excluded_folder_never_synced_even_if_ticked(sl, monkeypatch):
    """白名单误勾「北斗小说资料」：只同步合法目录，落点目录内文档一律不拉。"""
    documents = [
        {"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)},
        {"id": 999, "title": "北斗设定集", "folderId": 3, "updatedAt": _to_iso(2)},
    ]
    _patch_xuanji_call(monkeypatch, documents=documents)
    cfg = await _make_config(sl, xj_sync_folders="设定集,北斗小说资料")

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert out["added"] == 1
    assert [f["name"] for f in out["folders"]] == ["设定集"]
    items = await _items(sl)
    assert [i.xj_doc_id for i in items] == ["101"]

    # 只勾落点目录 → 零同步（防自我循环）；每用户仅一行配置，故原地改白名单
    async with sl() as db:
        cfg2 = (await db.execute(select(IntegrationConfig))).scalars().first()
        cfg2.xj_sync_folders = "北斗小说资料"
        await db.commit()
    async with sl() as db:
        out2 = await xj_sync.sync_from_xuanji(await db.merge(cfg2), db)
    assert (out2["added"], out2["updated"], out2["skipped"], out2["folders"]) == (0, 0, 0, [])
    assert len(await _folders(sl)) == 1, "除首轮镜像目录外不得再建任何目录"
    assert xj_sync.XJ_EXCLUDED_FOLDER == "北斗小说资料"


# ---------- r2⑦：重复执行幂等 ----------


@pytest.mark.asyncio
async def test_repeat_run_is_idempotent(sl, monkeypatch):
    """同一库连跑两轮：第二轮全 skipped，无重复插入；同一目录的两篇共用一个镜像目录。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[
            {"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)},
            {"id": 103, "title": "金手指", "folderId": 1, "updatedAt": _to_iso(3)},
            {"id": 102, "title": "人物卡", "folderId": 2, "updatedAt": _to_iso(2)},
        ],
        docs={
            101: {"id": 101, "title": "世界观", "content": "a"},
            103: {"id": 103, "title": "金手指", "content": "b"},
            102: {"id": 102, "title": "人物卡", "content": "c"},
        },
    )
    cfg = await _make_config(sl)

    async with sl() as db:
        first = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)
    async with sl() as db:
        second = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert first["added"] == 3
    assert [f["name"] for f in first["folders"]] == ["设定集", "素材库"]
    assert [f["docs"] for f in first["folders"]] == [2, 1], "同一目录两篇共用一个镜像目录"
    assert second == {
        "added": 0,
        "updated": 0,
        "protected": 0,
        "conflicts": 0,
        "skipped": 3,
        "failed": 0,
        "folders": [
            {"id": 1, "name": "设定集", "docs": 2},
            {"id": 2, "name": "素材库", "docs": 1},
        ],
    }
    assert len(await _items(sl)) == 3
    assert len(await _folders(sl)) == 2


# ---------- C1：条目经资料库路由保存后仍不脱锚 ----------


@pytest.mark.asyncio
async def test_item_saved_via_library_route_stays_synced(sl, monkeypatch):
    """C1 回归：镜像条目经真实 PUT 路由保存（ItemIn.source 默认 manual，条目的 source
    被冲成 manual）→ 再同步不得再插一条重复镜像，且本地标题（别名）不被覆盖。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑正文"}},
    )
    cfg = await _make_config(sl)

    async with sl() as db:  # 首轮：建镜像条目
        assert (await xj_sync.sync_from_xuanji(await db.merge(cfg), db))["added"] == 1

    async with sl() as db:  # 用户经真实路由保存（body 不含 source → 复现翻转）
        item = (await db.execute(select(LibraryItem))).scalars().one()
        user = (await db.execute(select(User).limit(1))).scalars().first()
        await library_mod.update_item(
            item.id, library_mod.ItemIn(title="我的别名", content="作者本地改过的正文"), user, db
        )
        assert (await db.get(LibraryItem, item.id)).source == "manual", "路由保存会把 source 冲成 manual"
        assert (await db.get(LibraryItem, item.id)).xj_doc_id == "101", "锚必须活下来"

    async with sl() as db:  # 再同步
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert out["added"] == 0, "source 被冲成 manual 后不得脱锚（否则会重复插入镜像）"
    assert out["skipped"] == 1
    items = await _items(sl)
    assert len([i for i in items if i.xj_doc_id == "101"]) == 1, "同一 xj_doc_id 只许一条"
    assert items[0].title == "我的别名", "本地标题=别名，入方向不得覆盖"
    assert items[0].content == "作者本地改过的正文", "dirty 未置位时不得被误判为可覆盖"


# ---------- m5：同名不同父目录各自计数 ----------


@pytest.mark.asyncio
async def test_same_name_folders_counted_separately(sl, monkeypatch):
    """m5：璇玑侧两个同名「设定集」（不同 id）各占一行、各自计数，不合并成虚高一行。"""
    dup_a = {"id": 1, "name": "设定集", "parentId": None, "icon": None}
    dup_b = {"id": 5, "name": "设定集", "parentId": 4, "icon": None}
    _patch_xuanji_call(
        monkeypatch,
        folders=[dup_a, dup_b, FOLDER_MATERIAL],
        documents=[
            {"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)},
            {"id": 105, "title": "支线设定", "folderId": 5, "updatedAt": _to_iso(2)},
        ],
        docs={
            101: {"id": 101, "title": "世界观", "content": "a"},
            105: {"id": 105, "title": "支线设定", "content": "b"},
        },
    )
    cfg = await _make_config(sl)

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert out["added"] == 2
    assert out["folders"] == [
        {"id": 1, "name": "设定集", "docs": 1},
        {"id": 5, "name": "设定集", "docs": 1},
        {"id": 2, "name": "素材库", "docs": 0},  # 勾选但无文档 → 计数 0，不虚高
    ], "同名不同 id 的目录必须各占一行，计数不得合并"
    assert len(await _items(sl)) == 2
    # 镜像按名复用：两个同名目录共用一个北斗公共库目录
    assert [f.name for f in await _folders(sl)] == ["设定集"]


# ---------- r2⑧：异常分支（单篇失败不炸整批 / getTree 失败上抛）----------


@pytest.mark.asyncio
async def test_single_document_failure_counted_and_batch_continues(sl, monkeypatch):
    """单篇 getDocument 失败 → 该篇计入 failed 跳过，其余文档照常入库。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[
            {"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)},
            {"id": 102, "title": "人物卡", "folderId": 2, "updatedAt": _to_iso(2)},
        ],
        fail_ids=(101,),
    )
    cfg = await _make_config(sl)

    async with sl() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert out["failed"] == 1
    assert out["added"] == 1
    assert [i.xj_doc_id for i in await _items(sl)] == ["102"]


@pytest.mark.asyncio
async def test_get_tree_failure_propagates(sl, monkeypatch):
    """getTree 失败自然上抛（循环容错是批E 的事，本批不吞）。"""
    async def boom(config, procedure, input_data=None):
        raise RuntimeError("璇玑不可达")

    monkeypatch.setattr(integ_mod, "_xuanji_call", boom)
    cfg = await _make_config(sl)

    async with sl() as db:
        with pytest.raises(RuntimeError):
            await xj_sync.sync_from_xuanji(await db.merge(cfg), db)
    async with sl() as db:
        assert (await db.get(IntegrationConfig, cfg.id)).xj_sync_last_at == "", "失败轮不写留痕"


# ---------- r2⑨：_diff_in 纯函数边界 ----------


def test_diff_in_empty_and_malformed_inputs():
    """空输入 / 缺 id / 非 dict 条目 / 异形 updatedAt → 不抛异常，分流可预期。"""
    assert xj_sync._diff_in([], {}) == ([], [], [], [])
    assert xj_sync._diff_in([], {"101": {"item_updated_at": _to_iso(1), "dirty": True}}) == ([], [], [], [])
    assert xj_sync._diff_in(None, None) == ([], [], [], [])  # type: ignore[arg-type]

    # 缺 id / 非 dict 条目：没有锚，四类分流里都不体现（既不新增也不计数）
    assert xj_sync._diff_in([{"title": "无 id"}, None, "垃圾"], {}) == ([], [], [], [])  # type: ignore[list-item]

    # 异形 updatedAt（None / 空串 / 数字 / dict）不炸：全无本地映射 → 一律 new
    weird = [
        {"id": 1, "title": "a", "updatedAt": None},
        {"id": 2, "title": "b", "updatedAt": ""},
        {"id": 3, "title": "c", "updatedAt": 12345},
        {"id": 4, "title": "d", "updatedAt": {"x": 1}},
    ]
    new, ow, prot, conf = xj_sync._diff_in(weird, {})
    assert [d["id"] for d in new] == [1, 2, 3, 4]
    assert (ow, prot, conf) == ([], [], [])

    # 本地基线异形（None）而璇玑有时间戳 → 视为变更；本地脏 → conflict
    _, ow2, _, conf2 = xj_sync._diff_in(
        [{"id": 1, "title": "a", "updatedAt": _to_iso(2)}],
        {"1": {"item_updated_at": None, "dirty": True}},
    )
    assert ow2 == [] and [d["id"] for d in conf2] == [1]

    # 璇玑缺时间戳而基线非空 → 保守视为未变：干净 skip，脏 → protect
    _, ow3, prot3, conf3 = xj_sync._diff_in(
        [{"id": 1, "title": "a", "updatedAt": None}, {"id": 2, "title": "b", "updatedAt": ""}],
        {"1": {"item_updated_at": _to_iso(9), "dirty": False}, "2": {"item_updated_at": _to_iso(9), "dirty": True}},
    )
    assert (ow3, conf3) == ([], [])
    assert [d["id"] for d in prot3] == [2]


def test_diff_in_four_way_split():
    """四类分流齐活：new / overwrite / protect / conflict，且各自只出现一次。"""
    docs = [
        {"id": 1, "title": "新", "updatedAt": _to_iso(1)},          # 本地无 → new
        {"id": 2, "title": "覆盖", "updatedAt": _to_iso(5)},        # 变了 + 干净 → overwrite
        {"id": 3, "title": "保护", "updatedAt": _to_iso(1)},        # 未变 + 脏 → protect
        {"id": 4, "title": "冲突", "updatedAt": _to_iso(6)},        # 变了 + 脏 → conflict
        {"id": 5, "title": "跳过", "updatedAt": _to_iso(1)},        # 未变 + 干净 → skip
    ]
    local = {
        "2": {"item_updated_at": _to_iso(1), "dirty": False},
        "3": {"item_updated_at": _to_iso(1), "dirty": True},
        "4": {"item_updated_at": _to_iso(1), "dirty": True},
        "5": {"item_updated_at": _to_iso(1), "dirty": False},
    }
    new, ow, prot, conf = xj_sync._diff_in(docs, local)
    assert [d["id"] for d in new] == [1]
    assert [d["id"] for d in ow] == [2]
    assert [d["id"] for d in prot] == [3]
    assert [d["id"] for d in conf] == [4]


# ---------- 白名单解析纯函数 ----------


def test_parse_folder_names():
    """逗号（含全角）分隔、去空白、丢空项，同时剔除硬排除目录。"""
    assert xj_sync._parse_folder_names(" 设定集 , 素材库 ,, ") == {"设定集", "素材库"}
    assert xj_sync._parse_folder_names("设定集，素材库") == {"设定集", "素材库"}
    assert xj_sync._parse_folder_names("北斗小说资料") == set()
    assert xj_sync._parse_folder_names("") == set()
    assert xj_sync._parse_folder_names(None) == set()  # type: ignore[arg-type]


# ---------- m3：epoch 毫秒归一化 ----------


def test_norm_ts_epoch_ms_defense():
    """m3：纯数字（int/float/数字串）按 epoch 毫秒折算成 UTC ISO，ISO 串原样保留。

    没有这条防御时，「璇玑发 epoch ms、本地存 ISO」混形会被字典序误判成「未变」，
    真更新被静默跳过。
    """
    EPOCH_MS = 1791194400000  # = 2026-10-05T10:00:00Z
    assert xj_sync._norm_ts(EPOCH_MS) == "2026-10-05T10:00:00.000000Z"
    assert xj_sync._norm_ts(str(EPOCH_MS)) == "2026-10-05T10:00:00.000000Z"
    assert xj_sync._norm_ts(12345) == "1970-01-01T00:00:12.345000Z"
    assert xj_sync._norm_ts("12345") == "1970-01-01T00:00:12.345000Z"
    # 超范围/异形退回原样字符串，绝不抛
    assert xj_sync._norm_ts(1e30) == "1e+30"
    assert xj_sync._norm_ts(-1e30) == "-1e+30"
    assert xj_sync._norm_ts(float("inf")) == "inf"
    assert xj_sync._norm_ts(True) == "True", "bool 是 int 子类，但不当 epoch"
    # ISO 串原样保留（不重写），空值仍视为「未变」
    assert xj_sync._norm_ts(_to_iso(1)) == _to_iso(1)
    assert xj_sync._norm_ts(None) == ""

    # 行为回归①：epoch ms 比 ISO 基线晚 → 必须判「变了」（干净 → overwrite）
    _, ow, _, conf = xj_sync._diff_in(
        [{"id": 1, "title": "a", "updatedAt": EPOCH_MS}],
        {"1": {"item_updated_at": _to_iso(1), "dirty": False}},
    )
    assert [d["id"] for d in ow] == [1]
    # 行为回归②：同上但本地脏 → conflict（而非静默 protect）
    _, _, _, conf2 = xj_sync._diff_in(
        [{"id": 1, "title": "a", "updatedAt": str(EPOCH_MS)}],
        {"1": {"item_updated_at": _to_iso(1), "dirty": True}},
    )
    assert [d["id"] for d in conf2] == [1]
    # 收敛性：基线存的是归一化后的形态，同刻再跑判「未变」
    _, ow3, _, conf3 = xj_sync._diff_in(
        [{"id": 1, "title": "a", "updatedAt": EPOCH_MS}],
        {"1": {"item_updated_at": "2026-10-05T10:00:00.000000Z", "dirty": True}},
    )
    assert (ow3, conf3) == ([], [])


# ---------- m1：镜像条目进资料库 FTS ----------


@pytest.mark.asyncio
async def test_mirror_item_indexed_in_library_fts(sl_fts, monkeypatch):
    """m1：镜像条目落库后同步进 library_items_fts，/api/library/search 搜得到。

    ⚠ 分词硬约束（沿用 tests/test_library_search_scope.py 的实测钉死）：unicode61
    把连续 CJK 串当**单个 token**，检索词必须恰好对齐完整 token 串——故检索词放
    content 句首、后接全角标点。
    """
    from app.search_fts import search_library_fts

    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
        docs={101: {"id": 101, "title": "世界观", "content": "灵气复苏，剑修昌盛。"}},
    )
    cfg = await _make_config(sl_fts)

    async with sl_fts() as db:
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)
    assert out["added"] == 1

    async with sl_fts() as db:
        rows = (await db.execute(text("SELECT rowid, scope, title FROM library_items_fts"))).fetchall()
        hits = await search_library_fts(db, "灵气复苏", allowed_novel_ids=set())  # 只搜公共库
        item_id = (await db.execute(select(LibraryItem.id))).scalar_one()

    assert [r[0] for r in rows] == [item_id], "rowid 必须是 library_items.id"
    assert rows[0][1] == -1, "公共库在 FTS 里记 scope=-1"
    assert [h["title"] for h in hits] == ["世界观"]
    assert hits[0]["source"] == "xuanji"
    assert "灵气复苏" in hits[0]["snippet"]
