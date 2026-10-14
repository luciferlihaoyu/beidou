"""kb-sync 写方向自动化（t1-t3）回归测试：

- t1：同步到璇玑的设定集包含大纲——有 novel_id 时把 OutlineNode 展开为
  「## 大纲（卷章骨架）」缩进树；否则回退 outline_json「## 大纲快照」；整段截断 16000。
- t2：kb-sync 主体抽成 _do_kb_sync 共用函数；路由成功后写 last_kb_sync_at 留痕；
  未配置璇玑地址时 400。
- t3：定稿后自动同步——finalize_chapter 经 BackgroundTasks 调度后台函数；
  后台函数自建 SessionLocal；auto_kb_sync 关 / 无 xuanji_url 静默跳过；
  _do_kb_sync 抛异常时绝不上抛（失败绝不阻塞定稿）。
- t4：立项自动预填 kb_query——confirm 落库后用 book_spec 组装默认检索词
  （书名 + genre + theme/premise 前 40 字，截 200），已有 kb_query 不覆盖。
- t5：生成时确定性注入本书设定集——_recall_own_spec 按标题精确匹配
  「北斗·书名·设定集」取 content 截 600，异常一律返回 None；
  组装上下文时注入「本书设定集」段（记忆卡之后、kb_query 泛搜之前），
  且 IntegrationConfig 只查一次供两段共用。
- 加固（独立评审 minor）：书名口径统一到 ai_extras._book_title（上传/召回同源，
  带空白与异形 titles 不再隐性空转）；大纲环残团每节点恰好输出一次；
  截断段恰好 16000；「后台函数→真打包→假 mcp」端到端不断链。
"""

import json

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db as app_db
import app.routers.ai_factory as factory
import app.routers.ai_extras as extra
import app.routers.integrations as integ_mod
from app.deps import count_words
from app.models import (
    AiChapterJob,
    AiProject,
    Base,
    Chapter,
    IntegrationConfig,
    Novel,
    OutlineNode,
    User,
)


# ---------- 共享 helper：内存库 + 造数 ----------


@pytest_asyncio.fixture
async def sl():
    """独立内存库（Base.metadata.create_all 直接带新列，覆盖新库迁移口径）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _patch_xuanji(monkeypatch, *, folder_id: int | None = 7):
    """Mock 璇玑 MCP 通道：ensure_folder 返回固定目录 id；mcp 捕获 document_upsert 参数。"""
    recorded: dict = {"tools": [], "args": []}

    async def fake_ensure_folder(config, name):
        return folder_id

    async def fake_mcp(config, tool, args):
        recorded["tools"].append(tool)
        recorded["args"].append(args)
        return {"ok": True}

    monkeypatch.setattr(integ_mod, "_xuanji_ensure_folder", fake_ensure_folder)
    monkeypatch.setattr(integ_mod, "_xuanji_mcp", fake_mcp)
    return recorded


async def _make_project(sl: async_sessionmaker, *, novel_id: int | None = None, **kw) -> tuple[User, AiProject]:
    async with sl() as db:
        user = User(username="kb-tester", password_hash="x")
        db.add(user)
        await db.flush()
        project = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙", novel_id=novel_id, **kw)
        db.add(project)
        await db.commit()
        return user, project


async def _make_novel(sl: async_sessionmaker, user_id: int) -> Novel:
    async with sl() as db:
        novel = Novel(user_id=user_id, title="剑起青萍")
        db.add(novel)
        await db.commit()
        return novel


def _add_nodes(sl: async_sessionmaker, novel_id: int):
    """两卷两章的乱序 sort_order 树：序卷(sort 1) → 第一卷(sort 2) → 章挂第一卷下。"""

    async def _run():
        async with sl() as db:
            vol0 = OutlineNode(novel_id=novel_id, title="序卷", sort_order=1)
            vol1 = OutlineNode(novel_id=novel_id, title="第一卷 风起", sort_order=2)
            db.add_all([vol0, vol1])
            await db.flush()
            db.add_all(
                [
                    OutlineNode(
                        novel_id=novel_id, parent_id=vol1.id, title="第一章 出山",
                        content="少年背剑下山", sort_order=3,
                    ),
                    OutlineNode(novel_id=novel_id, parent_id=vol1.id, title="第二章 入世", sort_order=4),
                ]
            )
            await db.commit()

    return _run()


# ---------- t1：大纲打包 ----------


def test_outline_tree_rendered_with_indent():
    """OutlineNode 扁平列表（已按 sort_order 排序）→ 缩进树；孤儿节点按根挂。"""
    vol0 = OutlineNode(id=11, title="序卷", sort_order=1)
    vol1 = OutlineNode(id=12, title="第一卷 风起", sort_order=2)
    ch1 = OutlineNode(id=13, parent_id=12, title="第一章 出山", content="少年背剑下山", sort_order=3)
    ch2 = OutlineNode(id=14, parent_id=12, title="第二章 入世", sort_order=4)
    orphan = OutlineNode(id=15, parent_id=999, title="孤章", sort_order=5)

    text = extra._render_outline_tree([vol0, vol1, ch1, ch2, orphan])

    lines = text.splitlines()
    assert lines == [
        "- 序卷",
        "- 第一卷 风起",
        "  - 第一章 出山：少年背剑下山",
        "  - 第二章 入世",
        "- 孤章",
    ]


def test_outline_tree_survives_parent_cycles():
    """环残团：A↔B 互为 parent + A→A 自环 → 每节点恰好输出一次，绝不死循环也不丢数据。"""
    a = OutlineNode(id=1, parent_id=2, title="甲", sort_order=1)
    b = OutlineNode(id=2, parent_id=1, title="乙", content="乙的内容", sort_order=2)
    self_loop = OutlineNode(id=3, parent_id=3, title="自环", sort_order=3)
    root = OutlineNode(id=4, title="根", sort_order=4)
    leaf = OutlineNode(id=5, parent_id=4, title="叶", content="叶的内容", sort_order=5)

    text = extra._render_outline_tree([a, b, self_loop, root, leaf])

    lines = text.splitlines()
    # 根树正常展开；环团（从 None 不可达）平铺收尾
    assert lines == ["- 根", "  - 叶：叶的内容", "- 甲", "- 乙：乙的内容", "- 自环"]
    for title in ("甲", "乙", "自环", "根", "叶"):
        assert sum(1 for ln in lines if title in ln) == 1, f"{title} 应恰好输出一次"


def test_book_title_unified_across_boundaries():
    """书名口径（组B minor1）：titles 首个非空 strip 串；None/非列表/[""] 一律兜底 seed_prompt[:20]。"""
    p = AiProject(seed_prompt="少年逆袭成剑仙")

    assert extra._book_title(p, {"titles": [" 我欲成魔 "]}) == "我欲成魔"  # strip 生效
    assert extra._book_title(p, {"titles": [None, " 备选名 "], "genre": "玄幻"}) == "备选名"  # 跳过非串项
    assert extra._book_title(p, {"titles": [""]}) == "少年逆袭成剑仙"
    assert extra._book_title(p, {"titles": ["", "  "]}) == "少年逆袭成剑仙"
    assert extra._book_title(p, {"titles": []}) == "少年逆袭成剑仙"
    assert extra._book_title(p, {"titles": "不是列表"}) == "少年逆袭成剑仙"
    assert extra._book_title(p, {"titles": None}) == "少年逆袭成剑仙"
    assert extra._book_title(p, {}) == "少年逆袭成剑仙"
    assert extra._book_title(p, None) == "少年逆袭成剑仙"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_whitespace_title_sync_and_recall_agree(sl, monkeypatch):
    """书名接线跨边界（minor-1）：**异形 titles** 下上传标题与召回检索词一字不差。

    两轮都走真实 ``_do_kb_sync``（上传）与真实 ``_recall_own_spec``（召回）：

    - ① ``titles=[" 我欲成魔 "]``：strip 生效，两侧同为「北斗·我欲成魔·设定集」；
    - ② ``titles=[None, "备选名"]``：跳过非串首项取备选名——两侧同为「北斗·备选名·设定集」。

    ② 是关键一钉：它只在**上传与召回共用同一个 _book_title** 时成立。若召回侧退回
    旧的本地口径（``str(titles[0]).strip()``），检索词会变成「北斗·None·设定集」，
    而上传侧仍写「北斗·备选名·设定集」——同步成功却永远召不回（隐性空转）。
    """
    recorded = _patch_xuanji(monkeypatch)
    user, p = await _make_project(sl)
    async with sl() as db:
        p = await db.get(AiProject, p.id)
        p.book_spec_json = json.dumps({"titles": [" 我欲成魔 "], "genre": "玄幻"})
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        db.add(kc)
        await db.commit()

        result = await extra._do_kb_sync(p, kc, db)

    # 写方向：上传标题 strip 生效
    assert result["title"] == "北斗·我欲成魔·设定集"
    assert recorded["args"][0]["title"] == "北斗·我欲成魔·设定集"

    # 读方向：召回侧同口径（检索词与上传标题一字不差）
    doc_title = "北斗·我欲成魔·设定集"
    calls = _patch_xuanji_call(
        monkeypatch, docs=[{"id": 3, "title": doc_title}], document={"title": doc_title, "content": "设定"}
    )
    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        kc2 = (await db.execute(select(IntegrationConfig))).scalars().first()
        out = await factory._recall_own_spec(p2, kc2)

    assert out is not None, "上传用的标题必须能被召回侧精确命中"
    assert calls[0] == ("kb.searchDocuments", {"query": doc_title})

    # ② 异形 titles：[None, "备选名"] → 两个边界都必须是「北斗·备选名·设定集」
    recorded2 = _patch_xuanji(monkeypatch)  # 重新装假通道，拿到干净的上传记录
    async with sl() as db:
        p3 = await db.get(AiProject, p.id)
        p3.book_spec_json = json.dumps({"titles": [None, "备选名"], "genre": "玄幻"})
        kc_alt = (await db.execute(select(IntegrationConfig))).scalars().first()
        await db.commit()

        result2 = await extra._do_kb_sync(p3, kc_alt, db)

    alt_title = "北斗·备选名·设定集"
    assert result2["title"] == alt_title
    assert recorded2["args"][0]["title"] == alt_title, "非串首项被抓成「None」就是这里红"

    calls2 = _patch_xuanji_call(
        monkeypatch, docs=[{"id": 4, "title": alt_title}], document={"title": alt_title, "content": "设定"}
    )
    async with sl() as db:
        p4 = await db.get(AiProject, p.id)
        kc3 = (await db.execute(select(IntegrationConfig))).scalars().first()
        out2 = await factory._recall_own_spec(p4, kc3)

    assert out2 is not None, "异形 titles 下上传的文档也必须召得回"
    assert calls2[0] == ("kb.searchDocuments", {"query": alt_title}), "召回检索词必须与上传标题同源"


@pytest.mark.asyncio
async def test_do_kb_sync_includes_outline_tree(sl, monkeypatch):
    """有 novel_id 时：查 OutlineNode 展开为缩进树上传，标题带「## 大纲（卷章骨架）」。"""
    recorded = _patch_xuanji(monkeypatch)
    user, p = await _make_project(sl)
    novel = await _make_novel(sl, user.id)
    p.novel_id = novel.id
    async with sl() as db:
        await db.merge(p)
        await db.commit()
    await _add_nodes(sl, novel.id)

    async with sl() as db:
        p = await db.get(AiProject, p.id)
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        db.add(kc)
        await db.commit()

        result = await extra._do_kb_sync(p, kc, db)

    assert result["ok"] is True
    assert result["title"] == "北斗·少年逆袭成剑仙·设定集"  # 标题口径：book_spec titles → seed_prompt[:20]
    assert recorded["tools"] == ["document_upsert"]
    content = recorded["args"][0]["content"]
    assert "## 大纲（卷章骨架）" in content
    assert "- 序卷" in content
    assert "  - 第一章 出山：少年背剑下山" in content
    assert recorded["args"][0].get("folderId") == 7


@pytest.mark.asyncio
async def test_do_kb_sync_falls_back_to_outline_snapshot(sl, monkeypatch):
    """无 novel_id 时回退 outline_json：段标题「## 大纲快照」+ 原文。"""
    recorded = _patch_xuanji(monkeypatch)
    user, p = await _make_project(sl, outline_json='{"volumes": ["第一卷 风起"]}')
    async with sl() as db:
        p = await db.get(AiProject, p.id)
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        db.add(kc)
        await db.commit()

        await extra._do_kb_sync(p, kc, db)

    content = recorded["args"][0]["content"]
    assert "## 大纲快照" in content
    assert '{"volumes": ["第一卷 风起"]}' in content
    assert "## 大纲（卷章骨架）" not in content


@pytest.mark.asyncio
async def test_do_kb_sync_outline_truncated(sl, monkeypatch):
    """超长大纲整段截断到 16000 字符（含段标题行）。"""
    captured: dict = {}

    async def fake_mcp(config, tool, args):
        captured.update(args)
        return {"ok": True}

    async def fake_folder(config, name):
        return None

    monkeypatch.setattr(integ_mod, "_xuanji_mcp", fake_mcp)
    monkeypatch.setattr(integ_mod, "_xuanji_ensure_folder", fake_folder)

    user, p = await _make_project(sl, outline_json="很" * 30000)
    async with sl() as db:
        p = await db.get(AiProject, p.id)
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        db.add(kc)
        await db.commit()

        await extra._do_kb_sync(p, kc, db)

    content = captured["content"]
    seg = "## 大纲快照\n" + content.split("## 大纲快照\n", 1)[1]
    assert len(seg) == 16000, f"大纲段应整段硬截 16000，实际 {len(seg)}"


# ---------- t2：共用函数 + 留痕字段 ----------


@pytest.mark.asyncio
async def test_kb_sync_route_stamps_last_sync(sl, monkeypatch):
    """路由成功后返回原结构，并把 last_kb_sync_at 写成 UTC ISO 字符串。"""
    recorded = _patch_xuanji(monkeypatch)
    user, p = await _make_project(sl, outline_json='{"volumes": ["第一卷"]}')
    async with sl() as db:
        p = await db.get(AiProject, p.id)
        db.add(IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example"))
        await db.commit()

        result = await extra.kb_sync(p.id, user, db)

    assert result == {
        "ok": True,
        "title": "北斗·少年逆袭成剑仙·设定集",
        # 独立口径：chars 应等于 fake mcp 实际收到的上传正文长度（不是拿 chars 反算 chars）
        "chars": len(recorded["args"][0]["content"]),
        "result": {"ok": True},
    }
    assert result["chars"] > 0
    assert "## 大纲" in recorded["args"][0]["content"]
    async with sl() as db2:  # 独立 session 验证落库，避开 identity map 缓存
        p2 = await db2.get(AiProject, p.id)
        assert p2.last_kb_sync_at != "", "路由成功后应写 last_kb_sync_at"
        assert "T" in p2.last_kb_sync_at  # ISO 形态


@pytest.mark.asyncio
async def test_kb_sync_route_requires_xuanji_url(sl):
    """未配置璇玑（无配置行 / xuanji_url 为空）→ 400，且不写留痕。"""
    user, p = await _make_project(sl)
    async with sl() as db:
        p = await db.get(AiProject, p.id)
        with pytest.raises(HTTPException) as ei:
            await extra.kb_sync(p.id, user, db)
        assert ei.value.status_code == 400
        assert p.last_kb_sync_at == ""

    async with sl() as db:
        p = await db.get(AiProject, p.id)
        db.add(IntegrationConfig(user_id=user.id, xuanji_url=""))  # 有行但地址为空
        await db.commit()
        with pytest.raises(HTTPException) as ei2:
            await extra.kb_sync(p.id, user, db)
        assert ei2.value.status_code == 400


# ---------- t3：定稿后自动同步 ----------


async def _make_finalize_fixtures(sl: async_sessionmaker) -> tuple[User, AiProject, AiChapterJob]:
    """定稿链路全套数据：User/Novel/Chapter(job)/AiProject(status=writing)。"""
    async with sl() as db:
        user = User(username="fin-tester", password_hash="x")
        db.add(user)
        await db.flush()
        novel = Novel(user_id=user.id, title="定稿测试书")
        db.add(novel)
        await db.flush()
        chapter = Chapter(novel_id=novel.id, title="第一章", status="draft")
        db.add(chapter)
        await db.flush()
        p = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙", novel_id=novel.id, status="writing")
        db.add(p)
        await db.flush()
        job = AiChapterJob(project_id=p.id, chapter_id=chapter.id, status="writing")
        db.add(job)
        await db.commit()
        return user, p, job


def _patch_finalize_llm(monkeypatch):
    """finalize 的 LLM 面：配置/状态文件/关系同步/摘要全部 mock 掉。"""
    from types import SimpleNamespace

    async def fake_pick_config(user, db, route_field):
        return SimpleNamespace(id=1, base_url="https://llm.example", api_key="k", model="m")

    async def fake_update_state(p, chapter, text, config, db):
        return True

    async def fake_sync_relations(p, novel, text, config, db):
        return 0

    async def fake_chat_text(config, system, prompt, max_tokens=0):
        return "一句话摘要"

    monkeypatch.setattr(factory, "_pick_config", fake_pick_config)
    monkeypatch.setattr(factory, "_update_state_files", fake_update_state)
    monkeypatch.setattr(factory, "_sync_relations_from_chapter", fake_sync_relations)
    monkeypatch.setattr(factory, "_chat_text", fake_chat_text)


@pytest.mark.asyncio
async def test_finalize_schedules_auto_kb_sync(sl, monkeypatch):
    """定稿成功 → 返回原结构，且后台注册 _auto_kb_sync_after_finalize(p.id, user.id)。"""
    _patch_finalize_llm(monkeypatch)
    recorded = _patch_xuanji(monkeypatch)
    from app.routers.ai_factory import FinalizeIn, finalize_chapter

    user, p, job = await _make_finalize_fixtures(sl)
    bg = BackgroundTasks()
    text = "少年背剑下山，一剑破开长空。" * 3

    async with sl() as db:
        result = await finalize_chapter(p.id, job.id, FinalizeIn(content_text=text), bg, user, db)

    assert result == {
        "ok": True,
        # 独立口径：正文转 HTML 入书稿后重新数一遍，不用返回值自证
        "word_count": count_words(factory._text_to_html(text)),
        "state_updated": True,
        "split_into": 1,
    }
    task = bg.tasks[0]
    assert task.func.__name__ == "_auto_kb_sync_after_finalize"
    assert task.args == (p.id, user.id)


@pytest.mark.asyncio
async def test_auto_sync_success_stamps_timestamp(sl, monkeypatch):
    """后台函数成功路径：自建 session 调 _do_kb_sync 并写 last_kb_sync_at。"""
    user, p = await _make_project(sl)
    async with sl() as db:
        db.add(IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example"))
        await db.commit()

    monkeypatch.setattr(app_db, "SessionLocal", sl)  # 后台函数自建 session 落到测试库
    called: dict = {}

    async def fake_do(p, kc, db):
        called["project"] = p.id
        return {"ok": True, "title": "t", "chars": 3, "result": {}}

    monkeypatch.setattr(extra, "_do_kb_sync", fake_do)

    await extra._auto_kb_sync_after_finalize(p.id, user.id)

    assert called["project"] == p.id
    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        assert p2.last_kb_sync_at != ""


@pytest.mark.asyncio
async def test_auto_sync_skipped_when_disabled(sl, monkeypatch):
    """auto_kb_sync=False 或 xuanji_url 为空 → 静默返回，绝不调 _do_kb_sync。"""
    user, p = await _make_project(sl, auto_kb_sync=False)
    async with sl() as db:
        db.add(IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example"))
        await db.commit()

    monkeypatch.setattr(app_db, "SessionLocal", sl)
    called: dict = {"n": 0}

    async def fake_do(p, kc, db):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(extra, "_do_kb_sync", fake_do)

    await extra._auto_kb_sync_after_finalize(p.id, user.id)
    assert called["n"] == 0

    # 开关打开但 xuanji_url 为空 → 同样跳过
    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        p2.auto_kb_sync = True
        await db.commit()
        kc = (await db.execute(select(IntegrationConfig))).scalars().first()
        kc.xuanji_url = ""
        await db.commit()

    await extra._auto_kb_sync_after_finalize(p.id, user.id)
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_auto_sync_swallows_exceptions(sl, monkeypatch):
    """_do_kb_sync 抛异常 → 后台函数不上抛（失败绝不阻塞定稿）。"""
    user, p = await _make_project(sl)
    async with sl() as db:
        db.add(IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example"))
        await db.commit()

    monkeypatch.setattr(app_db, "SessionLocal", sl)

    async def boom(p, kc, db):
        raise RuntimeError("璇玑炸了")

    monkeypatch.setattr(extra, "_do_kb_sync", boom)

    await extra._auto_kb_sync_after_finalize(p.id, user.id)  # 不应抛出

    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        assert p2.last_kb_sync_at == ""  # 失败不写留痕


@pytest.mark.asyncio
async def test_auto_sync_end_to_end_packs_and_uploads(sl, monkeypatch):
    """组装链路端到端（不 mock _do_kb_sync）：后台函数 → 真打包 → 假 mcp 收到上传。

    守护「后台函数只调了一次打包、打包内容含「## 大纲」「## 立项」、标题格式正确」——
    只 mock 单侧会漏掉「函数被 mock 掉、真实链路根本没接上」这类断链。
    """
    recorded = _patch_xuanji(monkeypatch)
    monkeypatch.setattr(app_db, "SessionLocal", sl)

    user, p = await _make_project(sl, outline_json='{"volumes": ["第一卷 风起"]}')
    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        p2.book_spec_json = json.dumps({"titles": ["剑起青萍"], "genre": "玄幻", "premise": "少年得剑灵传承。"})
        db.add(IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example"))
        await db.commit()

    await extra._auto_kb_sync_after_finalize(p.id, user.id)

    assert recorded["tools"] == ["document_upsert"]  # 真实链路确实发出了上传（不是 mock 掉了）
    args = recorded["args"][0]
    assert args["title"] == "北斗·剑起青萍·设定集"
    content = args["content"]
    assert "## 大纲" in content
    assert "## 立项" in content
    assert '{"volumes": ["第一卷 风起"]}' in content
    assert args.get("folderId") == 7

    async with sl() as db:  # 成功路径同样写留痕
        p3 = await db.get(AiProject, p.id)
        assert p3.last_kb_sync_at != ""


# ---------- t4：立项自动预填 kb_query ----------


def test_default_kb_query_pure_function():
    """t4③ 纯函数：书名 + genre + theme/premise 各截 40，整段截 200；空/异形 spec 安全降级不炸。"""
    spec = {
        "titles": ["剑起青萍", "备选名"],
        "genre": "玄幻",
        "theme": "凡人流逆天改命",
        "premise": "少年得剑灵传承，踏上逆天之路。" * 10,
    }
    q = factory._default_kb_query(spec)
    assert q.startswith("剑起青萍 玄幻 凡人流逆天改命 ")
    assert len(q) <= 200

    # 整段硬截 200
    assert len(factory._default_kb_query({"titles": ["长" * 150], "genre": "玄" * 100})) == 200

    # 空 spec / 缺字段 / 异形字段 → 空串或安全值，绝不抛
    assert factory._default_kb_query({}) == ""
    assert factory._default_kb_query(None) == ""  # type: ignore[arg-type]
    assert factory._default_kb_query({"titles": []}) == ""
    assert factory._default_kb_query({"titles": "不是列表", "genre": "科幻"}) == "科幻"
    assert factory._default_kb_query({"titles": ["", "  "], "premise": "只有梗概"}) == "只有梗概"
    # 组B minor3：非字符串书名不得被 str() 变成 "None" 混进检索词
    assert factory._default_kb_query({"titles": [None], "genre": "科幻"}) == "科幻"
    assert factory._default_kb_query({"titles": [None, "备选"], "genre": "科幻"}) == "科幻"
    assert not factory._default_kb_query({"titles": [None]}).startswith("None")


@pytest.mark.asyncio
async def test_confirm_book_spec_prefills_kb_query(sl):
    """t4①：confirm 保存 book_spec 后 kb_query 非空、含书名且 ≤200 字。"""
    from app.routers.ai_factory import BookSpecConfirmIn

    async with sl() as db:
        user = User(username="kb-prefill", password_hash="x")
        db.add(user)
        await db.flush()
        p = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙", status="draft")
        db.add(p)
        await db.commit()

        await factory.confirm_book_spec(
            p.id,
            BookSpecConfirmIn(
                title="剑起青萍",
                book_spec={"titles": ["剑起青萍"], "genre": "玄幻", "premise": "少年得剑灵传承，逆天改命。"},
            ),
            user,
            db,
        )

    async with sl() as db2:  # 独立 session 验证落库
        p2 = await db2.get(AiProject, p.id)
        assert p2.kb_query != "", "confirm 后应自动预填 kb_query"
        assert "剑起青萍" in p2.kb_query
        assert len(p2.kb_query) <= 200


@pytest.mark.asyncio
async def test_confirm_keeps_existing_kb_query(sl):
    """t4②：已有 kb_query（非空）的项目 confirm 后不被覆盖。"""
    from app.routers.ai_factory import BookSpecConfirmIn

    async with sl() as db:
        user = User(username="kb-keep", password_hash="x")
        db.add(user)
        await db.flush()
        p = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙", kb_query="作者自定义检索词", status="draft")
        db.add(p)
        await db.commit()

        await factory.confirm_book_spec(
            p.id,
            BookSpecConfirmIn(title="剑起青萍", book_spec={"titles": ["剑起青萍"], "genre": "玄幻"}),
            user,
            db,
        )
        assert p.kb_query == "作者自定义检索词"


# ---------- t5：生成时确定性注入本书设定集 ----------


def _patch_xuanji_call(monkeypatch, *, docs=None, document=None, err: Exception | None = None):
    """Mock _xuanji_call（_recall_own_spec 函数内 import，patch 模块属性即生效）：按 procedure 返回预设值并记录调用。"""
    recorded: list[tuple[str, dict]] = []

    async def fake_call(config, procedure, input_data=None):
        if err is not None:
            raise err
        recorded.append((procedure, dict(input_data or {})))
        if procedure == "kb.searchDocuments":
            return docs if docs is not None else []
        if procedure == "kb.getDocument":
            return document
        return None

    monkeypatch.setattr(integ_mod, "_xuanji_call", fake_call)
    return recorded


@pytest.mark.asyncio
async def test_recall_own_spec_exact_title_match(sl, monkeypatch):
    """t5①：title 完全匹配的第 1 篇 → getDocument 取 content 截 600，格式带「本书设定集」头。"""
    doc_title = "北斗·少年逆袭成剑仙·设定集"  # 书名兜底 seed_prompt[:20]
    recorded = _patch_xuanji_call(
        monkeypatch,
        docs=[{"id": 8, "title": "北斗·别的书·设定集"}, {"id": 9, "title": doc_title}],
        document={"title": doc_title, "content": "核心设定" * 200},  # 800 字 → 截 600
    )
    user, p = await _make_project(sl)

    async with sl() as db:
        p = await db.get(AiProject, p.id)
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        out = await factory._recall_own_spec(p, kc)

    assert out is not None
    assert out.startswith("【本书设定集（璇玑同步，一致性最高优先）】")
    assert doc_title in out
    assert len(out.split("：", 1)[1]) == 600  # content 硬截 600
    # 搜索词即精确标题；取的是匹配那篇的 id
    assert recorded[0] == ("kb.searchDocuments", {"query": doc_title})
    assert recorded[1][0] == "kb.getDocument"
    assert recorded[1][1] == {"id": 9}


@pytest.mark.asyncio
async def test_recall_own_spec_skips_non_exact_title(sl, monkeypatch):
    """t5②：返回列表里 title 不完全匹配（前后缀不一致）→ 不取，返回 None。"""
    recorded = _patch_xuanji_call(
        monkeypatch,
        docs=[{"id": 1, "title": "北斗·少年逆袭成剑仙·设定集（旧版）"}, {"id": 2, "title": "北斗·少年逆袭·设定集"}],
        document={"title": "不该取到", "content": "x"},
    )
    user, p = await _make_project(sl)

    async with sl() as db:
        p = await db.get(AiProject, p.id)
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        out = await factory._recall_own_spec(p, kc)

    assert out is None
    assert len(recorded) == 1  # 只搜了一次，绝不 getDocument


@pytest.mark.asyncio
async def test_recall_own_spec_swallows_exceptions(sl, monkeypatch):
    """t5③：璇玑调用抛异常 → 返回 None 绝不上抛。"""
    _patch_xuanji_call(monkeypatch, err=RuntimeError("璇玑炸了"))
    user, p = await _make_project(sl)

    async with sl() as db:
        p = await db.get(AiProject, p.id)
        kc = IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example")
        assert await factory._recall_own_spec(p, kc) is None


class _CountingSession:
    """薄代理：统计 execute 次数与 IntegrationConfig 查询次数，其余属性直通真实 session。"""

    def __init__(self, db):
        self._db = db
        self.integration_config_queries = 0

    async def execute(self, stmt, *args, **kwargs):
        if "integration_config" in str(stmt).lower():
            self.integration_config_queries += 1
        return await self._db.execute(stmt, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._db, name)


@pytest.mark.asyncio
async def test_assemble_context_includes_own_spec(sl, monkeypatch):
    """t5④：集成路径——配置璇玑且标题命中时，组装出的生成上下文含「本书设定集」段，与泛搜并存。"""
    doc_title = "北斗·定稿测试书·设定集"
    recorded = _patch_xuanji_call(
        monkeypatch,
        docs=[{"id": 5, "title": doc_title}],
        document={"title": doc_title, "content": "主角剑心通明；反派为上古剑魔。"},
    )
    user, p, job = await _make_finalize_fixtures(sl)
    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        p2.book_spec_json = json.dumps({"titles": ["定稿测试书"], "genre": "玄幻"})
        p2.kb_query = "剑仙"  # 泛搜同时启用，验证两段并存
        db.add(IntegrationConfig(user_id=user.id, xuanji_url="https://xj.example"))
        await db.commit()
    novel_id = p.novel_id

    async with sl() as db:
        p2 = await db.get(AiProject, p.id)
        novel = await db.get(Novel, novel_id)
        chapter = await db.get(Chapter, job.chapter_id)
        counting = _CountingSession(db)
        text = await factory._assemble_context(p2, novel, chapter, job, counting)

    assert "【本书设定集（璇玑同步，一致性最高优先）】" in text
    assert doc_title in text
    assert ("kb.searchDocuments", {"query": doc_title}) in recorded
    # 组B minor2：注入段与泛搜段共用同一次 IntegrationConfig 查询（原先各查一次）
    assert counting.integration_config_queries == 1
