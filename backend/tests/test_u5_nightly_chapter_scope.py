"""u5 项三：夜间流水线两处取章必须带归属作用域（Chapter.novel_id == p.novel_id）。

改动前的形状（nightly.py:326 与 :481 逐字同一行）：
    chapter = await db.get(Chapter, job.chapter_id)
只按主键取章，**不校验该章是否属于本项目的小说**。app/deps.py 头注自述的原始洞正是
「拿别人的章 id 就能读写」，本批已在读取侧重构了共享入口（ensure_chapter_in_project），
但这两个**写入侧**取点从未接入。

可达性（如实口径，不夸大）：全仓 AiChapterJob 只有 4 处创建
（chapter_split.py:136 / ai_import.py:143 / ai_factory.py:1331 / :1513），**全部用服务端
刚建出来的新章 id**，没有任何一处接受客户端传来的 chapter_id ⇒ 当前不存在把任务挂到
他人章节上的通路，属「潜伏形状、非现网可利用」。但一旦将来出现「把已有章节挂进任务」
的功能，这两个取点就是通的——所以本文件按"脏数据已存在"的形状钉死防线。

本文件钉住（期望值硬编码，不从实现 import 常量）：
① helper load_chapter_in_project：跨小说 → None；同小说 → 命中；项目未绑小说/空 id → None；
② 脏 job（project_id 属甲、chapter_id 指向乙的章）→ 夜间取章取不到，**且乙章正文一字未变**
   （不是"没参与"，是"没有写入"）；
③ 同一脏形状在后台批量连跑（第二处取点）同样取不到，且脏 job 确实进了 pending
   （total==1 证明循环真的跑到了取章点，不是空转出来的绿）；
④ 正常归属的 job 行为与改前一致：生成器收到的正是那一章、章号也照旧。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.nightly as nightly
from app import deps
from app.models import AiChapterJob, AiProject, Base, Chapter, Novel, User

DIRTY_CONTENT = "乙书正文：这行字一个字符都不许被夜跑改掉"
DIRTY_WORDS = 10


@pytest_asyncio.fixture
async def sl():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(sl, *, dirty: bool) -> dict:
    """甲项目（novel_id=甲书）；dirty=True 时 job 指向乙书的章（deps.py:125 警告的形状）。"""
    async with sl() as db:
        owner = User(username="u5-owner-%s" % dirty, password_hash="x")
        other = User(username="u5-other-%s" % dirty, password_hash="x")
        db.add_all([owner, other])
        await db.flush()
        jia = Novel(user_id=owner.id, title="[AI]甲书")
        yi = Novel(user_id=other.id, title="乙书")
        db.add_all([jia, yi])
        await db.flush()
        yi_ch = Chapter(novel_id=yi.id, title="乙的章", content=DIRTY_CONTENT, word_count=DIRTY_WORDS)
        jia_ch = Chapter(novel_id=jia.id, title="甲的章", content="", word_count=0)
        db.add_all([yi_ch, jia_ch])
        await db.flush()
        project = AiProject(user_id=owner.id, seed_prompt="少年逆袭", status="writing", novel_id=jia.id)
        db.add(project)
        await db.flush()
        job = AiChapterJob(
            project_id=project.id,
            chapter_id=(yi_ch.id if dirty else jia_ch.id),
            status="pending",
        )
        db.add(job)
        await db.commit()
        return {
            "pid": project.id,
            "uid": owner.id,
            "job_id": job.id,
            "yi_id": yi_ch.id,
            "jia_id": jia_ch.id,
            "jia_novel_id": jia.id,
        }


@pytest.fixture
def calls(monkeypatch, sl):
    """把 nightly 的外部依赖就地换掉：会话工厂 / 大纲续写钩子 / 单章生成。"""
    recorded = []

    async def _no_outline(p, db):
        return None

    async def _fake_gen(p, novel, job, chapter, num, db, on_retry=None):
        recorded.append({"job_id": job.id, "chapter_id": chapter.id, "num": num})
        return {"title": "第%s章" % num, "ok": True, "words": 0, "deai_score": 90}

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(nightly, "SessionLocal", sl)
    monkeypatch.setattr(nightly, "maybe_auto_continue_outline", _no_outline)
    monkeypatch.setattr(nightly, "_generate_one_with_retry", _fake_gen)
    monkeypatch.setattr(nightly, "_update_state_files", _noop, raising=False)
    return recorded


# ---------- ① helper 本身（数据层取章助手）----------


@pytest.mark.asyncio
async def test_helper_cross_novel_returns_none(sl):
    ids = await _seed(sl, dirty=True)
    async with sl() as db:
        p = await db.get(AiProject, ids["pid"])
        got = await deps.load_chapter_in_project(db, p, ids["yi_id"])
    assert got is None, "跨小说的章被取到了（id=%r）" % (getattr(got, "id", None),)


@pytest.mark.asyncio
async def test_helper_same_novel_hits(sl):
    ids = await _seed(sl, dirty=False)
    async with sl() as db:
        p = await db.get(AiProject, ids["pid"])
        got = await deps.load_chapter_in_project(db, p, ids["jia_id"])
    assert got is not None and got.id == ids["jia_id"], "同小说的章没取到：%r" % (got,)


@pytest.mark.asyncio
async def test_helper_unbound_or_empty_id_returns_none(sl):
    ids = await _seed(sl, dirty=False)
    async with sl() as db:
        p = await db.get(AiProject, ids["pid"])
        p.novel_id = None
        assert await deps.load_chapter_in_project(db, p, ids["jia_id"]) is None, "项目未绑小说仍能取章"
        p.novel_id = ids["jia_novel_id"]
        assert await deps.load_chapter_in_project(db, p, None) is None, "空 chapter_id 不许拿去查库"
        assert await deps.load_chapter_in_project(db, p, 0) is None, "chapter_id=0 不许拿去查库"


# ---------- ② 夜间取点：脏 job 必须取不到，且乙章一字未变 ----------


@pytest.mark.asyncio
async def test_nightly_skips_foreign_chapter_and_leaves_it_untouched(sl, calls):
    ids = await _seed(sl, dirty=True)
    report = await nightly.run_nightly_for_project(ids["pid"], ids["uid"])

    blob = str(report)
    assert "项目未就绪" not in blob and "小说不存在" not in blob, (
        "函数提前退出，脏 job 用例根本没跑到取章点：%s" % blob
    )
    assert calls == [], "脏 job 竟被送去生成（夜间取章没带归属作用域）：%r" % (calls,)
    async with sl() as db:
        fresh = await db.get(Chapter, ids["yi_id"])
        assert fresh.content == DIRTY_CONTENT, "乙的章节正文被夜跑写动了：%r" % (fresh.content,)
        assert fresh.word_count == DIRTY_WORDS, "乙的章节字数被改动了：%r" % (fresh.word_count,)


# ---------- ③ 第二处取点（后台批量连跑）同一形状 ----------


@pytest.mark.asyncio
async def test_batch_background_skips_foreign_chapter(sl, calls):
    ids = await _seed(sl, dirty=True)
    nightly.BG_TASKS[ids["pid"]] = {
        "running": True, "done": 0, "total": 0, "current": "",
        "results": [], "error": "", "stop_requested": False,
    }
    try:
        await nightly.run_batch_background(ids["pid"], ids["uid"], 5)
        entry = dict(nightly.BG_TASKS[ids["pid"]])
    finally:
        nightly.BG_TASKS.pop(ids["pid"], None)

    assert entry["total"] == 1, "脏 job 没进 pending，本用例没测到取章点：%r" % (entry,)
    assert calls == [], "脏 job 在后台连跑里被送去生成：%r" % (calls,)
    assert entry["done"] == 0, "脏 job 竟被计入完成：%r" % (entry,)
    async with sl() as db:
        fresh = await db.get(Chapter, ids["yi_id"])
        assert fresh.content == DIRTY_CONTENT, "乙的章节正文被后台连跑写动了：%r" % (fresh.content,)
        assert fresh.word_count == DIRTY_WORDS, "乙的章节字数被改动了：%r" % (fresh.word_count,)


# ---------- ④ 正常归属：行为与改前一致 ----------


@pytest.mark.asyncio
async def test_nightly_generates_scoped_chapter_as_before(sl, calls):
    ids = await _seed(sl, dirty=False)
    await nightly.run_nightly_for_project(ids["pid"], ids["uid"])
    assert calls == [{"job_id": ids["job_id"], "chapter_id": ids["jia_id"], "num": 1}], (
        "正常归属的章没被取到（或章号/调用次数变了）：%r" % (calls,)
    )
