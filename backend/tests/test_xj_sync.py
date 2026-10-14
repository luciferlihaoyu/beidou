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

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as db_mod
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


def _patch_xuanji_call(
    monkeypatch,
    *,
    documents=None,
    folders=None,
    docs=None,
    fail_ids=(),
    fail_exc=None,
    delay=0.0,
    after_push=None,
    mcp_fail=False,
    mcp_result=None,
):
    """Mock 璇玑双通道（批E 扩写）：

    - 读通道 ``_xuanji_call``：getTree 返回预设树；getDocument 按 id 返回全文，
      fail_ids 内的抛异常。**未知 id 返回 None**——与璇玑实测一致
      （``kb.getDocument`` 对不存在的 id 回 ``{"result":{"data":{"json":null}}}``，
      ``_xuanji_call`` 解包得 None，不抛异常）。
    - 写通道 ``_xuanji_mcp``：记录 ``(tool, args)`` 到 ``recorded["upserts"]``。
    - ``delay``：每次调用先 ``await asyncio.sleep(delay)``，用于并发用例制造
      真实交错窗口（否则协程一路跑到底，掩盖缺锁的重复插入）。
    - ``after_push``：一旦发生过 upsert，后续 getDocument 回读这版文档——
      模拟「写入后璇玑 updatedAt 前移」，用于钉基线刷新取的是回读值。
    """
    recorded: dict = {"calls": [], "get_document": [], "upserts": []}
    tree = {
        "folders": list(folders if folders is not None else [FOLDER_SPEC, FOLDER_MATERIAL, FOLDER_SPEC_SINK]),
        "documents": list(documents or []),
    }
    fulls = dict(DOC_FULL if docs is None else docs)
    after = dict(after_push or {})

    async def fake_call(config, procedure, input_data=None):
        if delay:
            await asyncio.sleep(delay)
        recorded["calls"].append(procedure)
        if procedure == "kb.getTree":
            return tree
        if procedure == "kb.getDocument":
            doc_id = (input_data or {}).get("id")
            recorded["get_document"].append(doc_id)
            if doc_id in fail_ids:
                raise fail_exc or RuntimeError("璇玑单篇读取炸了")
            source = after if (after and recorded["upserts"]) else fulls
            return source.get(doc_id)
        return None

    async def fake_mcp(config, tool, args):
        recorded["upserts"].append((tool, args))
        if mcp_fail:
            raise RuntimeError("璇玑写通道炸了")
        return dict(mcp_result) if mcp_result else {"id": 101, "action": "updated"}

    monkeypatch.setattr(integ_mod, "_xuanji_call", fake_call)
    monkeypatch.setattr(integ_mod, "_xuanji_mcp", fake_mcp)
    return recorded


async def _make_config(sl: async_sessionmaker, username: str | None = None, **kw) -> IntegrationConfig:
    """造一份集成配置（默认复用同一测试用户，避免 username 唯一约束撞车）。

    ``username`` 给定则用该用户（不存在则建）：一行 config 绑一个 user（``user_id`` 唯一），
    调度用例要同时摆好几份 config，只能按用户区分。
    """
    async with sl() as db:
        if username:
            user = (
                await db.execute(select(User).where(User.username == username))
            ).scalars().first()
        else:
            user = (await db.execute(select(User).limit(1))).scalars().first()
        if user is None:
            user = User(username=username or "xj-tester", password_hash="x")
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
            item.id,
            library_mod.ItemIn(title="我的别名", content="作者本地改过的正文"),
            BackgroundTasks(),  # 批E：update_item 新增后台任务参数（直接调用需显式传）
            user,
            db,
        )
        assert (await db.get(LibraryItem, item.id)).source == "manual", "路由保存会把 source 冲成 manual"
        assert (await db.get(LibraryItem, item.id)).xj_doc_id == "101", "锚必须活下来"

    async with sl() as db:  # 再同步
        out = await xj_sync.sync_from_xuanji(await db.merge(cfg), db)

    assert out["added"] == 0, "source 被冲成 manual 后不得脱锚（否则会重复插入镜像）"
    # 批E 起语义变化：这次保存**真的改了正文**，保存 hook 于是置了 xj_local_dirty，
    # 本轮就从「skipped（未变且干净）」落到「protected（未变但本地已改，保护不覆盖）》。
    # C1 要钉的仍然是「锚没脱 + 本地改动没被璇玑旧版盖掉」。
    assert (out["skipped"], out["protected"]) == (0, 1)
    items = await _items(sl)
    assert len([i for i in items if i.xj_doc_id == "101"]) == 1, "同一 xj_doc_id 只许一条"
    assert items[0].title == "我的别名", "本地标题=别名，入方向不得覆盖"
    assert items[0].content == "作者本地改过的正文", "本地已改（dirty）绝不被入方向覆盖"


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


# ======================================================================
# 批E（r3 出方向回写 + r4 API + r5 调度）
# ======================================================================
#
# 实测锚点（2026-10-14 探针打真璇玑，原文见交付报告）：
#   · ``document_upsert`` 入参 {title(必填,1-500), content, format, tags, metadata,
#     folderId}；语义「与**归一化标题**相同的**最早**那条就更新」——**标题即锚**；
#     返回 {"id": N, "action": "created"|"updated"}，**不含 updatedAt**；
#   · ``kb.getDocument {id}`` 返回体含 folderId 与 title（E1 无需 getTree 回退），
#     updatedAt 形如 ``2026-10-14T14:04:33.974Z``；**不存在的 id → json:null**
#     （_xuanji_call 解包得 None，不抛异常）。
#   故 E1：推本地别名（≠璇玑当前标题）会落到 action="created" 造**副本**，
#   回写必须按 xj_doc_id 解析璇玑当前标题起锚；基线因返回体无 updatedAt 必须回读。


@pytest.fixture(autouse=True)
def _fresh_sync_lock():
    """每个用例换一把全新的 ``_SYNC_LOCK``。

    asyncio 原语在**有竞争**的 acquire 里会被绑定到当时的事件循环，而
    pytest-asyncio 每个用例一个新循环——不换锁会让后续用例撞
    「... is bound to a different event loop」。生产是单循环长驻，无此问题。
    同一并清空调度退避表——它也是模块级进程内状态，不隔离会串台。
    """
    xj_sync._SYNC_LOCK = asyncio.Lock()
    xj_sync._SYNC_BACKOFF.clear()
    yield
    xj_sync._SYNC_BACKOFF.clear()


@pytest_asyncio.fixture
async def sl_file(tmp_path):
    """文件库夹具（每会话独立连接）——并发用例专用。

    内存库走 StaticPool（全家共用一条连接），两路并发会被连接层串行化，
    从而**掩盖**「缺锁 → 重复插入」；文件库每会话一条连接才是真实并发形态。
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'xj_conc.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def api(tmp_path):
    """临时文件库 + 真实路由 + 真实 JWT（鉴权走 create_token，不替换 get_current_user）。"""
    from app.deps import get_db
    from app.main import app as fastapi_app
    from app.security import create_token

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'xj_api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SL = async_sessionmaker(engine, expire_on_commit=False)
    async with SL() as s:
        user = User(username="xj-api", password_hash="x")
        s.add(user)
        await s.commit()
        uid, username = user.id, user.username

    async def fake_get_db():
        async with SL() as s:
            yield s

    fastapi_app.dependency_overrides[get_db] = fake_get_db
    client = AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test")
    yield SimpleNamespace(
        client=client,
        SL=SL,
        user_id=uid,
        headers={"Authorization": f"Bearer {create_token(uid, username)}"},
    )
    fastapi_app.dependency_overrides.clear()
    await client.aclose()
    await engine.dispose()


async def _caller(sl) -> User:
    """取夹具里那个测试用户（_make_config 建的那个）。"""
    async with sl() as db:
        return (await db.execute(select(User).limit(1))).scalars().first()


#: 调度判定的参照「现在」（表驱动用例共用）
NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------- ① E1：回写锚点取璇玑当前标题，绝不取本地别名 ----------


@pytest.mark.asyncio
async def test_push_anchors_on_xuanji_title_not_local_alias(sl, monkeypatch):
    """① E1 钉锚：本地别名 ≠ 璇玑当前标题 → upsert 的 title **必须**是璇玑标题。

    推别名会被璇玑当成新文档（实测 action="created" 且换新 id）= 造副本，
    正是本约束要防的；同时 item.title（作者别名）不得被回写流程改写。
    """
    recorded = _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑版", "folderId": 7, "updatedAt": _to_iso(3)}},
    )
    cfg = await _make_config(sl)
    item = await _make_item(sl, title="我的别名", content="本地改过的正文", xj_local_dirty=True)

    async with sl() as db:
        out = await xj_sync.push_item_to_xuanji(await db.merge(item), await db.merge(cfg), db)

    assert out == "pushed"
    tool, args = recorded["upserts"][0]
    assert tool == "document_upsert"
    assert args["title"] == "世界观", "E1：锚点必须取璇玑当前标题；取本地别名会在璇玑造副本"
    assert args["content"] == "本地改过的正文"
    assert args["folderId"] == 7, "folderId 按 xj_doc_id 解析出来后一并带上"

    fresh = (await _items(sl))[0]
    assert fresh.title == "我的别名", "本地别名不得被回写流程改写（口径 a）"


# ---------- ② 璇玑侧查无此文档 → missing + conflict ----------


@pytest.mark.asyncio
async def test_push_missing_document_flags_conflict(sl, monkeypatch):
    """② getDocument 回 null（文档不存在）→ 置 conflict、返回 missing，且**绝不写**。"""
    recorded = _patch_xuanji_call(monkeypatch, docs={})
    cfg = await _make_config(sl)
    item = await _make_item(sl, content="本地正文", xj_local_dirty=True)

    async with sl() as db:
        out = await xj_sync.push_item_to_xuanji(await db.merge(item), await db.merge(cfg), db)

    assert out == "missing"
    assert recorded["upserts"] == [], "锚不在就别写——写了会按标题新建一条文档"
    fresh = (await _items(sl))[0]
    assert fresh.xj_conflict is True
    assert fresh.xj_local_dirty is True, "待人工裁，dirty 保留"
    assert fresh.content == "本地正文"


# ---------- ③ 推送成功 → 清 dirty + 基线前移 ----------


@pytest.mark.asyncio
async def test_push_success_clears_dirty_and_refreshes_baseline(sl, monkeypatch):
    """③ 成功 → 清 dirty、基线刷新为「upsert 后回读」的 updatedAt。

    实测 upsert 返回体只有 {id, action}、**没有 updatedAt**；基线必须回读一次取
    写入后的时间戳，否则下一轮会把自家刚推的内容当「璇玑又变了」再覆盖回来。
    """
    doc = {"id": 101, "title": "世界观", "content": "璇玑旧版", "folderId": 1, "updatedAt": _to_iso(1)}
    recorded = _patch_xuanji_call(
        monkeypatch, docs={101: doc}, after_push={101: {**doc, "updatedAt": _to_iso(9)}}
    )
    cfg = await _make_config(sl)
    item = await _make_item(sl, content="本地新正文", xj_local_dirty=True, xj_doc_updated_at=_to_iso(1))

    async with sl() as db:
        out = await xj_sync.push_item_to_xuanji(await db.merge(item), await db.merge(cfg), db)

    assert out == "pushed"
    assert len(recorded["upserts"]) == 1
    assert recorded["get_document"][-1] == 101, "推送后要按 int id 回读基线"
    fresh = (await _items(sl))[0]
    assert fresh.xj_local_dirty is False
    assert fresh.xj_doc_updated_at == _to_iso(9), "基线取写入后回读的时间戳，不是推之前的"
    assert fresh.xj_conflict is False


@pytest.mark.asyncio
async def test_push_network_failure_keeps_dirty_and_baseline(sl, monkeypatch):
    """写通道炸了 → failed：dirty 保持（留待下轮批量兜底）、基线不动，绝不谎报成功。"""
    _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "世界观", "content": "x", "folderId": 1, "updatedAt": _to_iso(1)}},
        mcp_fail=True,
    )
    cfg = await _make_config(sl)
    item = await _make_item(sl, content="本地新正文", xj_local_dirty=True)

    async with sl() as db:
        out = await xj_sync.push_item_to_xuanji(await db.merge(item), await db.merge(cfg), db)

    assert out == "failed"
    fresh = (await _items(sl))[0]
    assert fresh.xj_local_dirty is True, "失败不许清 dirty"
    assert fresh.xj_doc_updated_at == _to_iso(1), "失败不许动基线"
    assert fresh.xj_conflict is False


@pytest.mark.asyncio
async def test_push_dirty_batch_selection_and_counts(sl, monkeypatch):
    """批量兜底只挑「dirty 且非冲突」的条目，四类计数各归各位。

    M9：混入一条**读通道故障**（104）来验 failed 计数与「dirty 保留」——
    比断言「一条本来就没脏过的条目不脏」有区分度得多。
    """
    doc = {"id": 101, "title": "世界观", "content": "x", "folderId": 1, "updatedAt": _to_iso(1)}
    recorded = _patch_xuanji_call(
        monkeypatch,
        docs={101: doc, 102: {**doc, "id": 102, "title": "人物卡"}},
        fail_ids={104},
        fail_exc=HTTPException(401, "Unauthorized"),
    )
    cfg = await _make_config(sl)
    await _make_item(sl, xj_doc_id="101", content="脏的", xj_local_dirty=True)  # → pushed
    await _make_item(sl, xj_doc_id="102", content="干净的", xj_local_dirty=False)  # → 不在候选
    await _make_item(sl, xj_doc_id="999", content="锚没了", xj_local_dirty=True)  # → missing
    await _make_item(sl, xj_doc_id="103", content="已冲突", xj_local_dirty=True, xj_conflict=True)  # → 不在候选
    await _make_item(sl, xj_doc_id="104", content="读不动", xj_local_dirty=True)  # → failed

    async with sl() as db:
        out = await xj_sync.push_dirty_batch(await db.merge(cfg), db)

    assert out == {"pushed": 1, "missing": 1, "failed": 1, "kept_dirty": 2}
    assert [args["title"] for _, args in recorded["upserts"]] == ["世界观"], "只有 101 该被写"
    by_doc = {i.xj_doc_id: i for i in await _items(sl)}
    assert by_doc["101"].xj_local_dirty is False, "推成功才清 dirty"
    assert (by_doc["999"].xj_conflict, by_doc["999"].xj_local_dirty) == (True, True), (
        "断锚：打冲突旗、dirty 留着等人工裁"
    )
    assert (by_doc["104"].xj_local_dirty, by_doc["104"].xj_conflict) == (True, False), (
        "读不动：dirty 必须保留、绝不打冲突旗（C1）"
    )
    assert by_doc["103"].xj_conflict is True, "冲突条目不自动推，等人工裁"
    assert by_doc["102"].xj_doc_updated_at == _to_iso(1), "没被选中的干净条目一个字节都不动"


# ---------- ④ 保存路由 hook ----------


@pytest.mark.asyncio
async def test_library_save_hook_dirty_only_on_content_change(sl, monkeypatch):
    """④ 真实 update_item：改正文 → dirty=True + 挂后台回写；只改标题 → 不置位；
    非同步条目（xj_doc_id==""）四列 xj_ 一个都不许碰（回归保护）。"""
    _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑版", "folderId": 1, "updatedAt": _to_iso(1)}},
    )
    await _make_config(sl, xj_sync_enabled=True)
    user = await _caller(sl)
    item = await _make_item(sl, content="本地旧正文")
    plain = await _make_item(sl, title="手工条目", content="手工正文", source="manual", xj_doc_id="")
    # 非同步条目的四个 xj_ 字段先拍快照，保存后必须**逐字段不变**
    before = [i for i in await _items(sl) if i.id == plain.id][0]
    before_state = (
        before.xj_doc_id,
        before.xj_local_dirty,
        before.xj_conflict,
        before.xj_doc_updated_at,
    )
    assert before_state[0] == "" and before_state[1] is False and before_state[2] is False

    # 只改标题（正文不变）→ 不置 dirty、不挂后台任务
    bt = BackgroundTasks()
    async with sl() as db:
        await library_mod.update_item(
            item.id,
            library_mod.ItemIn(title="我的别名", content="本地旧正文"),
            bt,
            await db.merge(user),
            db,
        )
    fresh = [i for i in await _items(sl) if i.id == item.id][0]
    assert fresh.title == "我的别名"
    assert fresh.xj_local_dirty is False, "只改标题是起别名，不回推（口径 a）"
    assert bt.tasks == [], "标题变化不触发回写"

    # 改正文 → dirty=True + 挂一单后台回写
    bt2 = BackgroundTasks()
    async with sl() as db:
        await library_mod.update_item(
            item.id,
            library_mod.ItemIn(title="我的别名", content="本地改过的正文"),
            bt2,
            await db.merge(user),
            db,
        )
    fresh = [i for i in await _items(sl) if i.id == item.id][0]
    assert fresh.xj_local_dirty is True
    assert len(bt2.tasks) == 1, "启用同步 → 保存即挂后台回写"
    assert bt2.tasks[0].func is xj_sync.push_after_save
    assert bt2.tasks[0].args == (item.id, user.id)

    # 非同步条目：一个 xj_ 字段都不许动
    bt3 = BackgroundTasks()
    async with sl() as db:
        await library_mod.update_item(
            plain.id,
            library_mod.ItemIn(title="手工条目", content="手工改过"),
            bt3,
            await db.merge(user),
            db,
        )
    p = [i for i in await _items(sl) if i.id == plain.id][0]
    assert p.content == "手工改过"
    assert (
        p.xj_doc_id,
        p.xj_local_dirty,
        p.xj_conflict,
        p.xj_doc_updated_at,
    ) == before_state, "非同步条目：四个 xj_ 字段一个都不许被保存链路碰到"
    assert bt3.tasks == []


@pytest.mark.asyncio
async def test_push_after_save_self_session_and_silence(sl, monkeypatch):
    """后台回写自建 SessionLocal（仿 t3 定稿钩子）：正常推走清 dirty；
    条目不存在 / 没有锚 / 未启用同步 → 静默返回，绝不抛。"""
    _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "世界观", "content": "x", "folderId": 1, "updatedAt": _to_iso(2)}},
    )
    cfg = await _make_config(sl, xj_sync_enabled=True)
    item = await _make_item(sl, content="本地新正文", xj_local_dirty=True)
    plain = await _make_item(sl, title="手工", content="手工", source="manual", xj_doc_id="")
    monkeypatch.setattr(db_mod, "SessionLocal", sl)

    await xj_sync.push_after_save(item.id, cfg.user_id)
    await xj_sync.push_after_save(plain.id, cfg.user_id)  # 无锚 → 静默
    await xj_sync.push_after_save(999999, cfg.user_id)  # 条目不存在 → 静默

    fresh = [i for i in await _items(sl) if i.id == item.id][0]
    assert fresh.xj_local_dirty is False
    plain_after = [i for i in await _items(sl) if i.id == plain.id][0]
    assert (plain_after.xj_doc_id, plain_after.xj_local_dirty) == ("", False), "无锚条目不动"


# ---------- ⑤ resolve 两种裁决 ----------


@pytest.mark.asyncio
async def test_resolve_keep_local_forces_push_and_clears_conflict(sl, monkeypatch):
    """⑤a keep_local：按 E1 把**本地版**强推回璇玑，清冲突 + 清 dirty，标题仍不动。"""
    recorded = _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "璇玑正名", "content": "璇玑版正文", "folderId": 1, "updatedAt": _to_iso(5)}},
    )
    await _make_config(sl, xj_sync_enabled=True)
    user = await _caller(sl)
    item = await _make_item(
        sl, title="我的别名", content="本地版正文", xj_local_dirty=True, xj_conflict=True, xj_doc_updated_at=_to_iso(1)
    )

    async with sl() as db:
        out = await library_mod.resolve_item(
            item.id, library_mod.ResolveIn(action="keep_local"), await db.merge(user), db
        )

    assert out == {"action": "keep_local", "outcome": "pushed", "item_id": item.id}
    fresh = (await _items(sl))[0]
    assert (fresh.xj_conflict, fresh.xj_local_dirty) == (False, False)
    assert fresh.content == "本地版正文", "keep_local = 保住本地版"
    assert fresh.title == "我的别名"
    assert recorded["upserts"][0][1]["title"] == "璇玑正名", "强回写同样按璇玑标题起锚（E1）"
    assert recorded["upserts"][0][1]["content"] == "本地版正文"


@pytest.mark.asyncio
async def test_resolve_take_xuanji_overwrites_content_keeps_alias(sl, monkeypatch):
    """⑤b take_xuanji：拉璇玑全文覆盖 content、刷基线、清双旗；标题保留本地别名（口径 a）。"""
    _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "璇玑正名", "content": "璇玑版正文", "folderId": 1, "updatedAt": _to_iso(5)}},
    )
    await _make_config(sl, xj_sync_enabled=True)
    user = await _caller(sl)
    item = await _make_item(
        sl, title="我的别名", content="本地版正文", xj_local_dirty=True, xj_conflict=True, xj_doc_updated_at=_to_iso(1)
    )

    async with sl() as db:
        out = await library_mod.resolve_item(
            item.id, library_mod.ResolveIn(action="take_xuanji"), await db.merge(user), db
        )

    assert out == {"action": "take_xuanji", "outcome": "taken", "item_id": item.id}
    fresh = (await _items(sl))[0]
    assert fresh.content == "璇玑版正文"
    assert fresh.title == "我的别名", "口径 (a)：标题保留本地别名"
    assert fresh.xj_doc_updated_at == _to_iso(5), "基线刷新到璇玑当前"
    assert (fresh.xj_conflict, fresh.xj_local_dirty) == (False, False)


@pytest.mark.asyncio
async def test_resolve_rejects_non_conflict_and_bad_action(sl, monkeypatch):
    """非冲突条目 / 非同步条目 / 未知 action → 400（别让人误裁正常条目）。"""
    _patch_xuanji_call(
        monkeypatch,
        docs={101: {"id": 101, "title": "世界观", "content": "x", "folderId": 1, "updatedAt": _to_iso(1)}},
    )
    await _make_config(sl, xj_sync_enabled=True)
    user = await _caller(sl)
    clean = await _make_item(sl, content="干净的")
    plain = await _make_item(sl, title="手工", content="手工", source="manual", xj_doc_id="")
    conflicted = await _make_item(
        sl, xj_doc_id="102", content="冲突中", xj_local_dirty=True, xj_conflict=True
    )

    async with sl() as db:
        u = await db.merge(user)
        for target in (clean.id, plain.id):
            with pytest.raises(HTTPException) as exc:
                await library_mod.resolve_item(target, library_mod.ResolveIn(action="keep_local"), u, db)
            assert exc.value.status_code == 400, target
        with pytest.raises(HTTPException) as exc2:
            await library_mod.resolve_item(conflicted.id, library_mod.ResolveIn(action="乱写"), u, db)
        assert exc2.value.status_code == 400

    untouched = [i for i in await _items(sl) if i.id == conflicted.id][0]
    assert untouched.xj_conflict is True, "非法 action 不得改动冲突旗"


# ---------- ⑥ m6：到期判定纯函数表驱动 ----------


@pytest.mark.parametrize(
    "last_at,interval,expect",
    [
        ("", 10, True),  # 从未同步 → 到期
        (None, 10, True),  # 可空列
        ("不是时间", 10, True),  # 畸形 → 视为到期（fail-open：宁可多跑，不可永不跑）
        ("2026-10-14T11:55:00+00:00", 10, False),  # 5 分钟前 < 10 分钟
        ("2026-10-14T11:50:00+00:00", 10, True),  # 恰好到点
        ("2026-10-14T11:49:59+00:00", 10, True),  # 已过
        ("2026-10-14T12:00:00.000Z", 10, False),  # Z 形态（基线形）
        ("2026-10-14T11:30:00Z", 60, False),  # Z 形态 30 分钟 < 60
        ("2026-10-14T11:00:00.000Z", 60, True),
        ("2026-10-14T11:00:00", 60, True),  # 裸时间按 UTC 处理
        ("2026-10-14T12:30:00+02:00", 60, True),  # m6 照妖镜：12:30+02:00 == 10:30Z
        ("2026-10-14T10:00:00+00:00", 0, True),  # interval<=0 → 按 1 分钟兜底
    ],
)
def test_xj_sync_due_table(last_at, interval, expect):
    """⑥ m6 到期判定：一律 ``datetime.fromisoformat`` 解析比较，绝不字符串比。

    倒数第三条是照妖镜：``12:30+02:00`` 实为 10:30Z（比 NOW 早 90 分钟 → 该跑），
    而字符串比较会给出「"12:00" < "12:30" → 未到」的**反向**结论。
    """
    assert xj_sync._xj_sync_due(last_at, interval, NOW) is expect


# ---------- ⑦ E2：并发保护 ----------


@pytest.mark.asyncio
async def test_concurrent_sync_converges_to_single_anchor(sl_file, monkeypatch):
    """⑦-a 端到端收敛（集成护栏）：两路并发 sync_all 跑完，同锚仍只 1 条、镜像目录 1 个。

    ⚠ **这条不是 E2 的红绿钉**（钉锁的是下面那条 ``..._holds_lock_across_whole_round``）：
    探针实测，两路对同一 sqlite 文件的写事务会被 SQLite 文件锁串行化——A 的 SELECT 一直
    等到 B 提交完才返回（实测时序：``A:getTree:exit → B:getTree:exit → B:getDocument →
    B:done:1 → A:done:0``，无锁也只得 1 条），重复窗口在本环境**无法稳定复现**，拿它
    当红绿会变成假绿。留着它是为守住「并发跑完仍收敛」这个集成结论。
    """
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑正文"}},
        delay=0.05,  # 尽量拉开两路时序，逼近并发窗口
    )
    cfg = await _make_config(sl_file)

    async def one_round() -> dict:
        async with sl_file() as db:
            return await xj_sync.sync_all(await db.merge(cfg), db)

    first, second = await asyncio.gather(one_round(), one_round())

    items = await _items(sl_file)
    assert [i.xj_doc_id for i in items] == ["101"], "同锚只许一条"
    assert [f.name for f in await _folders(sl_file)] == ["设定集"], "镜像目录不得重建"
    assert sorted([first["added"], second["added"]]) == [0, 1], "两轮合计只新增一条"


@pytest.mark.asyncio
async def test_sync_all_holds_lock_across_whole_round(monkeypatch):
    """⑦-b E2 钉锁（**决定性红绿**）：一轮进行中，第二路 ``sync_all`` 不得进入 ``sync_from_xuanji``。

    用替身把「一轮」变成可观测的进出事件：A 卡在轮内——正等价于「刚读完本地映射、尚未
    写库」那段读改写窗口——此时 B 调 ``sync_all`` 必须原地等锁。**撤掉这把锁**，B 会立刻
    闯进那段窗口（两路各持空映射 → 各插一条同锚镜像 = E2 要防的重复）。末尾再断言两轮被
    严格串行（A 走完连兜底回写一起，B 才进）。
    """
    inside, release = asyncio.Event(), asyncio.Event()
    order: list[str] = []
    seq = {"n": 0}
    empty = {
        "added": 0,
        "updated": 0,
        "protected": 0,
        "conflicts": 0,
        "skipped": 0,
        "failed": 0,
        "folders": [],
    }

    async def round_a(config, db):
        order.append("A:enter")
        inside.set()
        await release.wait()
        order.append("A:exit")
        return dict(empty)

    async def round_b(config, db):
        order.append("B:enter")
        return dict(empty)

    async def which_round(config, db):
        seq["n"] += 1
        return await (round_a(config, db) if seq["n"] == 1 else round_b(config, db))

    async def fake_push(config, db):
        order.append("push")
        return {"pushed": 0, "missing": 0, "failed": 0, "kept_dirty": 0}

    monkeypatch.setattr(xj_sync, "sync_from_xuanji", which_round)
    monkeypatch.setattr(xj_sync, "push_dirty_batch", fake_push)

    first = asyncio.create_task(xj_sync.sync_all(None, None))
    await inside.wait()  # A 已进轮内（持锁）
    second = asyncio.create_task(xj_sync.sync_all(None, None))
    await asyncio.sleep(0.05)  # 给 B 充分的闯锁机会
    assert order == ["A:enter"], f"E2：A 持锁期间 B 不得进入轮内，实测 {order}"

    release.set()
    await asyncio.gather(first, second)
    assert order == ["A:enter", "A:exit", "push", "B:enter", "push"], f"两轮须严格串行：{order}"


# ---------- r5：调度一跳只跑到期的 config ----------


@pytest.mark.asyncio
async def test_xj_sync_tick_only_runs_due_configs(sl, monkeypatch):
    """r5：一跳只跑「启用 + 配了 url/folders + 到点」的 config，其余原地不动。"""
    _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑正文"}},
    )
    recent = (NOW - timedelta(minutes=1)).isoformat()
    # 一行 config 绑一个 user（user_id 唯一），故四份 config 落在四个用户上
    due = await _make_config(sl, username="xj-tick-due", xj_sync_enabled=True, xj_sync_folders="设定集", xj_sync_last_at="")
    early = await _make_config(
        sl, username="xj-tick-early", xj_sync_enabled=True, xj_sync_folders="设定集", xj_sync_last_at=recent
    )
    off = await _make_config(sl, username="xj-tick-off", xj_sync_enabled=False, xj_sync_folders="设定集", xj_sync_last_at="")
    nolist = await _make_config(sl, username="xj-tick-nolist", xj_sync_enabled=True, xj_sync_folders="", xj_sync_last_at="")
    monkeypatch.setattr(db_mod, "SessionLocal", sl)

    assert await xj_sync._xj_sync_tick(NOW) == 1

    async with sl() as db:
        assert (await db.get(IntegrationConfig, due.id)).xj_sync_last_at != "", "到期的跑了并留痕"
        assert (await db.get(IntegrationConfig, early.id)).xj_sync_last_at == recent, "未到点的没跑"
        assert (await db.get(IntegrationConfig, off.id)).xj_sync_last_at == "", "没启用的没跑"
        assert (await db.get(IntegrationConfig, nolist.id)).xj_sync_last_at == "", "没勾文件夹的没跑"
    assert len(await _items(sl)) == 1, "只有到期那份落了镜像"


# ---------- ⑧⑨ r4 API ----------


@pytest.mark.asyncio
async def test_sync_endpoint_400_without_config_then_ok(api, monkeypatch):
    """⑧ 未配 url / 未勾文件夹 → 400；配齐 → 200 且返回入方向 7 键 + 出方向 3 键。"""
    r = await api.client.post("/api/integrations/xuanji/sync", headers=api.headers)
    assert r.status_code == 400, "未配璇玑地址必须 400"

    r = await api.client.put(
        "/api/integrations",
        json={"xuanji_url": "https://xj.example", "xj_sync_enabled": True, "xj_sync_folders": ""},
        headers=api.headers,
    )
    assert r.status_code == 200
    r = await api.client.post("/api/integrations/xuanji/sync", headers=api.headers)
    assert r.status_code == 400
    assert "文件夹" in r.json()["detail"], "没勾文件夹不许开跑（否则等于空转一轮）"

    recorded = _patch_xuanji_call(
        monkeypatch,
        documents=[{"id": 101, "title": "世界观", "folderId": 1, "updatedAt": _to_iso(1)}],
        docs={101: {"id": 101, "title": "世界观", "content": "璇玑正文"}},
    )
    r = await api.client.put(
        "/api/integrations",
        json={"xuanji_url": "https://xj.example", "xj_sync_enabled": True, "xj_sync_folders": "设定集"},
        headers=api.headers,
    )
    assert r.status_code == 200
    r = await api.client.post("/api/integrations/xuanji/sync", headers=api.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "added",
        "updated",
        "protected",
        "conflicts",
        "skipped",
        "failed",
        "folders",
        "pushed",
        "missing",
        "push_failed",
    }
    assert (body["added"], body["updated"], body["pushed"]) == (1, 0, 0)
    assert recorded["upserts"] == [], "新镜像条目不是脏的，不该有回写"


@pytest.mark.asyncio
async def test_config_roundtrip_and_interval_clamp(api):
    """⑨ config 四字段：三项可写 round-trip、last_at 只读回显；interval_min 钳位 5..1440。"""
    r = await api.client.put(
        "/api/integrations",
        json={
            "xuanji_url": "https://xj.example",
            "xj_sync_enabled": True,
            "xj_sync_folders": "设定集，素材库",
            "xj_sync_interval_min": 30,
        },
        headers=api.headers,
    )
    assert r.status_code == 200

    got = (await api.client.get("/api/integrations", headers=api.headers)).json()
    assert got["xj_sync_enabled"] is True
    assert got["xj_sync_folders"] == "设定集，素材库"
    assert got["xj_sync_interval_min"] == 30
    assert got["xj_sync_last_at"] == "", "初始为空（只读回显字段）"

    for value in (5, 1440):  # 边界值可写
        r = await api.client.put(
            "/api/integrations",
            json={"xuanji_url": "https://xj.example", "xj_sync_folders": "设定集", "xj_sync_interval_min": value},
            headers=api.headers,
        )
        assert r.status_code == 200, value
        shown = (await api.client.get("/api/integrations", headers=api.headers)).json()
        assert shown["xj_sync_interval_min"] == value

    for value in (4, 1441, 0, -10):  # 越界 → 422（Pydantic ge/le 钳位）
        r = await api.client.put(
            "/api/integrations",
            json={"xuanji_url": "https://xj.example", "xj_sync_folders": "设定集", "xj_sync_interval_min": value},
            headers=api.headers,
        )
        assert r.status_code == 422, value

    # last_at 只读：PUT 里塞它也不被接收
    async with api.SL() as db:
        cfg = (await db.execute(select(IntegrationConfig))).scalars().first()
        cfg.xj_sync_last_at = "2026-10-14T12:00:00+00:00"
        await db.commit()
    r = await api.client.put(
        "/api/integrations",
        json={
            "xuanji_url": "https://xj.example",
            "xj_sync_folders": "设定集",
            "xj_sync_interval_min": 10,
            "xj_sync_last_at": "1999-01-01T00:00:00+00:00",
        },
        headers=api.headers,
    )
    assert r.status_code == 200
    got = (await api.client.get("/api/integrations", headers=api.headers)).json()
    assert got["xj_sync_last_at"] == "2026-10-14T12:00:00+00:00", "last_at 只读，PUT 不得覆盖"


# ---------- C1 三态读：读不动 ≠ 文档没了 ----------


@pytest.mark.asyncio
async def test_push_read_failure_is_failed_not_conflict(sl, monkeypatch):
    """C1：读通道炸（401 / 网络 / 协议）→ failed，**保留 dirty、绝不置 conflict**。

    读不动只能说明「这一刻问不到璇玑」，不能推出「文档没了」。若误判 missing，
    会给作者错打一个「两边都改了，请裁决」的假冲突，逼他白做一次人工裁决；
    真按 missing 处理还会让条目退出批量兜底队列（conflict 条目不自动推）——永久卡住。
    """
    recorded = _patch_xuanji_call(
        monkeypatch, fail_ids={101}, fail_exc=HTTPException(401, "Unauthorized")
    )
    cfg = await _make_config(sl)
    item = await _make_item(sl, content="本地新正文", xj_local_dirty=True)

    async with sl() as db:
        out = await xj_sync.push_item_to_xuanji(await db.merge(item), await db.merge(cfg), db)

    assert out == "failed", "读通道故障必须归 failed（C1）"
    assert recorded["upserts"] == [], "读不动就别瞎猜标题去写（猜错=造副本）"
    fresh = (await _items(sl))[0]
    assert fresh.xj_local_dirty is True, "dirty 必须保留，等下轮自动重试"
    assert fresh.xj_conflict is False, "读通道故障不是冲突（C1）"
    assert fresh.xj_doc_updated_at == _to_iso(1), "失败不得动基线"
    assert fresh.content == "本地新正文"


@pytest.mark.asyncio
async def test_push_read_failure_recovers_on_next_round(sl, monkeypatch):
    """C1 后半：读通道恢复后，**下一轮批量兜底要能自动重推**这条（dirty 没被误清、没被错打冲突旗）。"""
    doc = {"id": 101, "title": "世界观", "content": "璇玑版", "folderId": 1, "updatedAt": _to_iso(1)}
    _patch_xuanji_call(
        monkeypatch, docs={101: doc}, fail_ids={101}, fail_exc=HTTPException(401, "Unauthorized")
    )
    cfg = await _make_config(sl)
    item = await _make_item(sl, content="本地新正文", xj_local_dirty=True)
    async with sl() as db:
        first = await xj_sync.push_item_to_xuanji(await db.merge(item), await db.merge(cfg), db)
    assert first == "failed"

    recorded = _patch_xuanji_call(monkeypatch, docs={101: doc})  # 读通道恢复（重新装假通道）
    async with sl() as db:
        out = await xj_sync.push_dirty_batch(await db.merge(cfg), db)

    assert out == {"pushed": 1, "missing": 0, "failed": 0, "kept_dirty": 0}, "恢复后应被自动重推"
    assert [args["title"] for _, args in recorded["upserts"]] == ["世界观"], "锚点仍是璇玑标题"
    fresh = (await _items(sl))[0]
    assert (fresh.xj_local_dirty, fresh.xj_conflict) == (False, False)


# ---------- M3 失败退避 ----------


@pytest.mark.parametrize(
    "fail_count,expect_min",
    [(0, 5), (1, 5), (2, 10), (3, 20), (4, 40), (5, 60), (6, 60), (99, 60)],
)
def test_backoff_minutes_table(fail_count, expect_min):
    """M3 退避档位表：5 → 10 → 20 → 40 → 60（封顶 1h），任何入参都不越界。"""
    assert xj_sync._backoff_minutes(fail_count) == expect_min


@pytest.mark.asyncio
async def test_xj_sync_tick_backs_off_after_failure(sl, monkeypatch):
    """M3：持续失败的 config 不再每 60s 撞墙——首次失败退避 5min，期满才再试。

    没有这张退避表时，一个 401 的 config 会每跳都打一次璇玑（每 60s 一记无效请求）。
    """
    calls = {"n": 0}

    async def boom(config, db):
        calls["n"] += 1
        raise RuntimeError("璇玑 401")

    monkeypatch.setattr(xj_sync, "sync_all", boom)
    await _make_config(sl, xj_sync_enabled=True, xj_sync_folders="设定集", xj_sync_last_at="")
    monkeypatch.setattr(db_mod, "SessionLocal", sl)

    assert await xj_sync._xj_sync_tick(NOW) == 0
    assert calls["n"] == 1, "首跳该真的试一次"

    assert await xj_sync._xj_sync_tick(NOW + timedelta(minutes=1)) == 0
    assert calls["n"] == 1, "退避期内（首败 +5min）不得再撞墙"

    assert await xj_sync._xj_sync_tick(NOW + timedelta(minutes=4, seconds=59)) == 0
    assert calls["n"] == 1, "差一秒到点也不许提前试"

    assert await xj_sync._xj_sync_tick(NOW + timedelta(minutes=5)) == 0
    assert calls["n"] == 2, "退避期满应再试一次"

    assert await xj_sync._xj_sync_tick(NOW + timedelta(minutes=9)) == 0
    assert calls["n"] == 2, "第二次失败翻倍到 10min：+9min 仍在退避内"
    assert await xj_sync._xj_sync_tick(NOW + timedelta(minutes=15)) == 0
    assert calls["n"] == 3, "第一败+5 与第二败+10 都期满 → 放行"


# ---------- M1 留空=不改 ----------


@pytest.mark.asyncio
async def test_config_put_omitted_sync_fields_are_untouched(api):
    """M1：PUT body 不含同步三键 → 已配置值原样保留（与 ``xuanji_api_key`` 同款「留空=不改」约定）。

    显式传值才写；清空白名单用**显式空串** ``""`` 表达。
    """
    ok = await api.client.put(
        "/api/integrations",
        json={
            "xuanji_url": "https://xj.example",
            "xj_sync_enabled": True,
            "xj_sync_folders": "设定集",
            "xj_sync_interval_min": 45,
        },
        headers=api.headers,
    )
    assert ok.status_code == 200

    # 只改 AList 地址，body 完全不带同步三键
    r = await api.client.put(
        "/api/integrations",
        json={"xuanji_url": "https://xj.example", "alist_url": "https://al.example"},
        headers=api.headers,
    )
    assert r.status_code == 200
    got = (await api.client.get("/api/integrations", headers=api.headers)).json()
    assert got["alist_url"] == "https://al.example", "同请求里显式给的字段照写"
    assert got["xj_sync_enabled"] is True, "省略 ≠ 关掉"
    assert got["xj_sync_folders"] == "设定集", "省略 ≠ 清空"
    assert got["xj_sync_interval_min"] == 45, "省略 ≠ 复位成默认值"

    # 显式传值才写：False / "" / 60 三个都要真落库
    r = await api.client.put(
        "/api/integrations",
        json={
            "xuanji_url": "https://xj.example",
            "xj_sync_enabled": False,
            "xj_sync_folders": "",
            "xj_sync_interval_min": 60,
        },
        headers=api.headers,
    )
    assert r.status_code == 200
    got = (await api.client.get("/api/integrations", headers=api.headers)).json()
    assert got["xj_sync_enabled"] is False, "显式 False 要写进去"
    assert got["xj_sync_folders"] == "", "显式空串 = 清空白名单"
    assert got["xj_sync_interval_min"] == 60
