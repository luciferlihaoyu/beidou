"""u0a：章节归属校验族收敛为单一入口 `ensure_chapter_in_project` 的守卫。

背景（同族洞 N 份拷贝）：
- ai_extras.deai-report 曾因 `if p.novel_id and chapter.novel_id != p.novel_id` 的短路分支，
  在项目解绑小说后允许任何人用 chapter_id 读整章正文（已修，4 档硬契约在
  test_deai_report.py::test_chapter_ownership_checked_on_every_tier，不许动）。
- batch.py / ai_factory.py 共 9 处只校验 `job.project_id != p.id`，随后
  `db.get(Chapter, job.chapter_id)` 不再校验章节归属 → 他人书的角色卡/书名等元数据
  被带进上下文（diagnose 一路实证），批量/SSE 路径 chapter.content 直接进审校 prompt。

归属语义（两条正向证据，任一成立即放行，不是串行"查不到才退回"）：
- A 项目直接证据：存在 AiChapterJob(project_id == p.id AND chapter_id == 该章)。
  这张表逐章绑死项目↔章节，是唯一不因解绑/换绑失真的证据。精确到那一条 job 那一章。
- B 绑定链证据：p.novel_id is not None 且 chapter.novel_id == p.novel_id。
  NULL 侧必须显式 `is not None` 参与，绝不许写成 `p.novel_id and ...` 短路。
- 两条都不成立 → 404（绝不 403，不暴露资源是否存在）。
- chapter_id 假值（0/None，orphan job 形状）→ 直接 404，不许拿去 db.get。

🔴 判据形状事实（实测 models.py，不是设计愿望）：
`Chapter.novel_id` 是 `Mapped[int]` + ForeignKey(ondelete="CASCADE")，**不可空**——
所以"忘记 NULL"的退化面只在 `p.novel_id` 一侧：若实现把 NULL 当 0 参与等值比较，
chapter.novel_id==0 的畸形章会被放行；档位⑤以 p.novel_id=NULL 直钉这一侧。

⏱ 容器纪律（单文件 <30 秒）——上一轮半成品 49 秒的病根与治法：
病根 = 给测试用户配了指向 http://127.0.0.1:9 的"真钥匙" AIConfig，漏网路径全部
走到上游等 ConnectError。本文件的快拒绝方式：**测试根本不建 AIConfig**。
`_pick_config` 在无配置时抛 400「请先在账号设置中配置 AI 接口」（纯 DB 查询，零网络），
于是"归属漏网"的最远可达点是这个瞬时 400——它本身就是泄露信号，配合
「响应绝不含他人书名/角色卡/正文」双判据；而"归属正确"的路径根本不该走到模型调用
（toctou 用例把 _chat_text 打成哨兵异常来钉这一点）。全程零网络请求。

测试形态：真 SQLite 文件库（conftest 的 DATA_DIR 隔离）+ TestClient +
dependency_overrides 打登录态。不打桩 db——假 DB 桩会把"多一次查询"这类实现变化
同形化掉（AGENTS.md 2026-11-16 教训）。
"""

import asyncio
from types import SimpleNamespace

import pytest

# 期望值硬编码在本文件（本项目铁律：不从实现 import 常量当基准）
SECRET_A = "<p>甲书绝密正文：林昭夜探王府。</p>" * 10
SECRET_B = "<p>绝密正文：乙一私藏内容，外人不可读。</p>" * 10
BOB_TITLE = "乙一·bob"
LEAK_MARKERS = ("乙一私藏内容", BOB_TITLE)


# ================================================================ 夹具


@pytest.fixture()
def client():
    """全新 sqlite 文件 + TestClient（每用例独立 tmp data_dir）。

    🔴 conftest 在模块级已导入 app.main 并建好引擎（DATA_DIR 指向会话级临时目录），
    这里不能像上轮那样 reload sys.modules 重建引擎——reload 全套 app.* 正是耗时大户之一。
    隔离靠每个用例 create_all + delete 清表：模型外键关系完整，全表删除即可回到空世界。
    """
    from fastapi.testclient import TestClient

    from app.db import Base, SessionLocal, engine
    from app.main import app

    async def _wipe():
        from sqlalchemy import delete

        from app import models

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with SessionLocal() as db:
            for table in reversed(Base.metadata.sorted_tables):
                await db.execute(delete(table))
            await db.commit()

    asyncio.run(_wipe())
    # 🔴 u0a 实测病根（间歇性卡死 3~20s 的真凶，非泄露也非断言）：TestClient 进入时
    # 会跑 app 的 lifespan，起两条**永不退出**的后台循环（_auto_backup_loop /
    # _xj_sync_loop，`while True: await asyncio.sleep(...)`）。退出时 anyio 的
    # blocking portal `join()` 与主线程在解释器 GIL 上形成竞态——同一进程内反复
    # 建/拆 portal（本文件 37 个用例各一次）时随机死锁。治法：**不触发 lifespan**，
    # 直接以 app 作为 ASGI transport 构造 client；本文件的判据全在路由函数体内，
    # 不需要任何 lifespan 副作用（备份/同步循环更不是被测面）。
    c = TestClient(app, raise_server_exceptions=False)
    yield c
    app.dependency_overrides.clear()


async def _mk_user(db, name):
    from app.models import User

    u = User(username=name, password_hash="x", role="user")
    db.add(u)
    await db.commit()
    return u


async def _mk_novel(db, owner_id, title):
    from app.models import Novel

    n = Novel(user_id=owner_id, title=title)
    db.add(n)
    await db.commit()
    return n


async def _mk_chapter(db, novel_id, title, content):
    from app.models import Chapter

    c = Chapter(novel_id=novel_id, title=title, content=content, sort_order=1)
    db.add(c)
    await db.commit()
    return c


async def _mk_project(db, user_id, novel_id):
    from app.models import AiProject

    p = AiProject(user_id=user_id, novel_id=novel_id, status="writing", seed_prompt="测试项目", book_spec_json="{}")
    db.add(p)
    await db.commit()
    return p


async def _mk_job(db, project_id, chapter_id, status="pending"):
    from app.models import AiChapterJob

    j = AiChapterJob(project_id=project_id, chapter_id=chapter_id, status=status)
    db.add(j)
    await db.commit()
    return j


@pytest.fixture()
def world(client):
    """两个用户各一本书、alice 的项目绑甲书 N1。

    🔴 刻意**不建 AIConfig**：任何走到模型调用的路径会在 _pick_config 处以 400
    瞬时失败（零网络）——这是"归属漏网"的最远可达点，也是泄露取证点。
    """

    async def _build():
        from app.db import SessionLocal

        async with SessionLocal() as db:
            ua = await _mk_user(db, "alice")
            ub = await _mk_user(db, "bob")
            na = await _mk_novel(db, ua.id, "甲书")
            nb = await _mk_novel(db, ub.id, "乙书")
            ca = await _mk_chapter(db, na.id, "甲一", SECRET_A)
            cb = await _mk_chapter(db, nb.id, BOB_TITLE, SECRET_B)
            proj = await _mk_project(db, ua.id, na.id)
            return {
                "client": client,
                "ua": ua,
                "ub": ub,
                "na": na,
                "nb": nb,
                "ca": ca,
                "cb": cb,
                "proj": proj,
            }

    return asyncio.run(_build())


def _as_user(world, user_row):
    """把 TestClient 请求打成指定登录用户（dependency_overrides，不动鉴权实现）。"""
    from app.deps import get_current_user
    from app.main import app

    async def _override():
        return user_row

    app.dependency_overrides[get_current_user] = _override


# ================================================================ 直测层：共享入口本体


class TestEnsureChapterInProject:
    """`app.deps.ensure_chapter_in_project(p, chapter_id, db) -> Chapter` 五+两档。"""

    @staticmethod
    def _call(p, chapter_id):
        from app.db import SessionLocal
        from app.deps import ensure_chapter_in_project

        async def _go():
            async with SessionLocal() as db:
                return await ensure_chapter_in_project(p, chapter_id, db)

        return asyncio.run(_go())

    @staticmethod
    async def _call_async(db, p, chapter_id):
        """入口直调的**同循环**版本：给「外层已在一个事件循环里」的用例用。

        🔴 上一版这里踩过坑：tier3/5/6 在外层 asyncio.run(_go()) 里又调 self._call
        （内部再 asyncio.run）→ RuntimeError「cannot be called from a running event
        loop」——红是红了，但红的是夹具而不是判据，等于零证据。
        """
        from app.deps import ensure_chapter_in_project

        return await ensure_chapter_in_project(p, chapter_id, db)

    def _fresh_p(self, world):
        from app.db import SessionLocal

        async def _go():
            async with SessionLocal() as db:
                from app.models import AiProject

                return await db.get(AiProject, world["proj"].id)

        return asyncio.run(_go())

    def test_tier1_same_book_chapter_passes(self, world):
        """① 本人项目 + 本书章节 → 放行并返回该章。"""
        p = self._fresh_p(world)
        ch = self._call(p, world["ca"].id)
        assert ch.id == world["ca"].id, "① 本书章节应放行"

    def test_tier2_cross_project_no_evidence_404(self, world):
        """② 跨项目拿别人小说的章节 id、与本项目无任何 job 关联 → 404。"""
        from fastapi import HTTPException

        p = self._fresh_p(world)
        with pytest.raises(HTTPException) as ei:
            self._call(p, world["cb"].id)
        assert ei.value.status_code == 404, f"跨项目应 404（不许 403/500），实得 {ei.value.status_code}"

    def test_tier3_cross_user_404(self, world):
        """③ 经典泄露形状：bob 的项目（绑乙书）拿 alice 的章节 id → 404。"""
        from fastapi import HTTPException
        from app.db import SessionLocal

        async def _go():
            async with SessionLocal() as db:
                pb = await _mk_project(db, world["ub"].id, world["nb"].id)
                return await self._call_async(db, pb, world["ca"].id)

        with pytest.raises(HTTPException) as ei:
            asyncio.run(_go())
        assert ei.value.status_code == 404, "跨用户 chapter_id 必须 404"

    def test_tier4_rebind_with_job_evidence_passes(self, world):
        """④ 换绑回归：p.novel_id=N2、历史章仍挂 N1，但 AiChapterJob 证明该章属本项目 → 放行。

        🔴 这条钉的是"一律比对 novel_id"的过度收紧（会把正常用户走死）：真实场景是
        用户解绑后重绑新书（SET NULL 只清外键、不删历史章节）。
        """
        from app.db import SessionLocal

        async def _go():
            async with SessionLocal() as db:
                from app.models import AiProject

                await _mk_job(db, world["proj"].id, world["ca"].id)
                n2 = await _mk_novel(db, world["ua"].id, "甲书新卷")
                p = await db.get(AiProject, world["proj"].id)
                p.novel_id = n2.id
                await db.commit()
                return await self._call_async(db, p, world["ca"].id)

        ch = asyncio.run(_go())
        assert ch.id == world["ca"].id, "④ 有 AiChapterJob 证据的换绑历史章必须放行"

    def test_tier5_null_novel_no_job_404(self, world):
        """⑤ p.novel_id 为 NULL 且无 job 证据 → 404（deai-report 原始泄露形状）。"""
        from fastapi import HTTPException
        from app.db import SessionLocal

        async def _go():
            async with SessionLocal() as db:
                from app.models import AiProject

                p = await db.get(AiProject, world["proj"].id)
                p.novel_id = None
                await db.commit()
                return await self._call_async(db, p, world["ca"].id)

        with pytest.raises(HTTPException) as ei:
            asyncio.run(_go())
        assert ei.value.status_code == 404, "⑤ novel_id=NULL 且无 job 证据必须 404"

    def test_tier6_job_evidence_is_per_chapter(self, world):
        """⑥ 证据粒度：job 只绑章 X，不能顺带放行另一本小说的章 Y。

        （防"项目有任一 job 就放行一切章"的 or 放宽变异。）
        """
        from fastapi import HTTPException
        from app.db import SessionLocal

        async def _go():
            async with SessionLocal() as db:
                from app.models import AiProject

                await _mk_job(db, world["proj"].id, world["ca"].id)
                n3 = await _mk_novel(db, world["ua"].id, "丙书")
                c3 = await _mk_chapter(db, n3.id, "丙一", "<p>丙书内容</p>")
                p = await db.get(AiProject, world["proj"].id)
                return await self._call_async(db, p, c3.id)

        with pytest.raises(HTTPException) as ei:
            asyncio.run(_go())
        assert ei.value.status_code == 404, "job 证据只对那条 job 的 chapter_id 生效"

    def test_tier7_nonexistent_chapter_404_not_500(self, world):
        """⑦ 探测面：不存在的 chapter_id → 404（非 500，不暴露存在性）。"""
        from fastapi import HTTPException

        p = self._fresh_p(world)
        with pytest.raises(HTTPException) as ei:
            self._call(p, 999_999)
        assert ei.value.status_code == 404

    def test_falsy_chapter_id_never_hits_db_get(self, world):
        """orphan 形状（chapter_id 为 0/None）→ 直接 404，不许拿去 db.get。"""
        from fastapi import HTTPException

        p = self._fresh_p(world)
        for cid in (None, 0):
            with pytest.raises(HTTPException) as ei:
                self._call(p, cid)
            assert ei.value.status_code == 404, f"chapter_id={cid!r} 必须 404"


# ================================================================ 端点层：泄露矩阵


ENDPOINT_LEAK_CASES = [
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/review-full", {}),
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/revise", {}),
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/deflavor", {}),
    # body 必须**同时**满足 pydantic 入参校验（excerpt min_length=10、content_text
    # min_length=20）与「直达归属点」两个形状：422 在路由函数之前返回，归属根本没被
    # 执行过——拿 422 当"挡住了"是误绿。这里用 bob 章正文里真实存在的片段做摘段，
    # 若归属漏网，该片段会原样进 AI prompt / 响应（泄露取证点）。
    # 🔴 u0a 实测修正：摘段曾写 "乙一私藏内容"（6 字 < min_length=10）→ pydantic 422
    # 把**请求回显**在 detail.input 里，泄露判据在 422 上假阳性命中、且归属根本没跑。
    # 换成 SECRET_B 里真实存在且 ≥10 字的整句（pos=0），保证请求先过校验、直达归属点。
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/rewrite-partial", {"excerpt": "绝密正文：乙一私藏内容，外人不可读。"}),
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/finalize", {"content_text": "<p>定稿正文占位，长度足够过校验。</p>" * 2}),
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/review", {}),
    # diagnose：🔴 碧霄裁决（u0a 二轮）——归属不成立一律 **404**，与本族其余 8 处同形。
    # （曾短暂实现「200 信息页 + ctx_chars=0」被驳回：撒谎形状 + 数值断言可被放宽 +
    #  一族两种契约。orphan（chapter_id=NULL）仍走信息页 200，那是无出路提示非越权。）
    ("get", "/api/ai-factory/projects/{pid}/jobs/{jid}/diagnose", None),
    ("post", "/api/ai-factory/projects/{pid}/jobs/{jid}/generate", None),
]
_EP_IDS = [p.rsplit("/", 1)[-1] for _, p, _ in ENDPOINT_LEAK_CASES]


async def _setup_foreign_job(world, mode):
    """alice 项目里塞一条 job.chapter_id 直指 bob 的章（攻击者经建任务 API 塞入）。

    mode="bound"：novel_id 保持 N1（B 证据不成立的唯一原因是章挂 N2）；
    mode="no_novel"：p.novel_id=NULL（deai-report 原始泄露形状）；
    mode="rebound"：p.novel_id=N2（换绑形状，纯等值判据在此必然两难）。
    """
    from app.db import SessionLocal

    async with SessionLocal() as db:
        from app.models import AiProject

        p = await db.get(AiProject, world["proj"].id)
        if mode == "rebound":
            n2 = await _mk_novel(db, world["ua"].id, "甲书新卷")
            p.novel_id = n2.id
        elif mode == "no_novel":
            p.novel_id = None
        await db.commit()
        job = await _mk_job(db, p.id, world["cb"].id)
        return p.id, job.id


@pytest.mark.parametrize("method,path,body", ENDPOINT_LEAK_CASES, ids=_EP_IDS)
@pytest.mark.parametrize("mode", ["bound", "no_novel", "rebound"])
def test_endpoints_reject_cross_user_chapter(world, method, path, body, mode):
    """跨用户直挂 job → 三个档位都必须被挡，且绝不吐 bob 的书名/角色卡/正文。

    判据形状事实（实测各端点守卫顺序）：review-full/finalize/review/generate/batch-run
    在归属点之前还有 `p.novel_id is None → 400「项目未关联小说」` 的业务守卫
    （test_ai_factory_pure.py 钉死的 UX 契约，不许为安全改动它）。所以：
      · no_novel 档合法结果 ∈ {400 业务守卫, 404 归属}（都被挡）；
      · bound/rebound 档必须直达归属点拿 **404**（diagnose 同形——碧霄裁决：一族一种
        形状，拒绝不许伪装成 200 空壳；orphan 提示走的是 chapter_id=NULL 分支，不在本矩阵）；
      · 三档共同硬底线：响应体绝不含 LEAK_MARKERS（乙书/苏挽云/断玉记）。
    快速拒绝保障：测试世界无 AIConfig → 漏网路径最远停在 _pick_config 的瞬时 400
    （零网络），但那已经是泄露事故——404 才是归属正确的形状。
    """
    w = world
    _as_user(w, w["ua"])
    pid, jid = asyncio.run(_setup_foreign_job(w, mode))
    url = path.format(pid=pid, jid=jid)
    resp = w["client"].request(method, url, json=body)
    for marker in LEAK_MARKERS:
        assert marker not in resp.text, f"{url} [{mode}] 响应泄露他人元数据/正文：{marker}"
    assert resp.status_code < 500, f"{url} [{mode}] 意外 5xx：{resp.text[:150]}"
    if mode in ("bound", "rebound"):
        assert resp.status_code == 404, (
            f"{url} [{mode}] 应 404（该章无法证明属于本项目），实得 {resp.status_code}：{resp.text[:120]}"
        )
    else:
        assert resp.status_code in (400, 404), (
            f"{url} [no_novel] 必须被挡（400 业务守卫或 404 归属），实得 {resp.status_code}"
        )


def test_batch_run_skips_foreign_chapter(world):
    """batch-run（SSE 批量循环）：pending 里混入指向他人章节的 job → 该章绝不进生成流。

    旧代码只在 chapter is None 时 continue，跨用户章畅通无阻（chapter_start 事件带
    他人章标题，后续 prompt 带他人角色卡）。修复后该 job 必须被跳过或整条流被挡；
    判据 = 事件流里绝无 bob 的 job_id / 乙书标记。
    """
    w = world
    _as_user(w, w["ua"])

    async def _seed():
        from app.db import SessionLocal

        async with SessionLocal() as db:
            j_evil = await _mk_job(db, w["proj"].id, w["cb"].id, status="pending")
            return j_evil.id

    j_evil_id = asyncio.run(_seed())
    resp = w["client"].post(f"/api/ai-factory/projects/{w['proj'].id}/batch-run?count=5")
    assert resp.status_code < 500, f"batch-run 意外 5xx：{resp.text[:200]}"
    if resp.status_code == 200:
        assert f'"job_id": {j_evil_id}' not in resp.text.replace('"job_id":', '"job_id":'), (
            f"越权 job {j_evil_id} 进入了批量流：{resp.text[:300]}"
        )
        assert f"{j_evil_id}" not in resp.text, f"越权 job {j_evil_id} 出现在 SSE 事件里"
    for marker in LEAK_MARKERS:
        assert marker not in resp.text, f"batch-run 泄露他人标记 {marker}"


def test_deai_report_honors_job_evidence_after_convergence(world):
    """ai_extras 收敛到同一入口后的第 5 档：换绑 + job 证据的历史章照常出报告。

    （旧等值判据在这里是 404——正是"换绑用户被永久判死"的回归位。
    现有 4 档硬契约仍在 test_deai_report.py，本条只补放行侧。）
    """
    w = world
    _as_user(w, w["ua"])

    resp = w["client"].get(f"/api/ai-factory/projects/{w['proj'].id}/deai-report?chapter_id={w['ca'].id}")
    assert resp.status_code == 200, f"① 本书章节应出报告，实得 {resp.status_code}：{resp.text[:150]}"

    async def _rebind():
        from app.db import SessionLocal

        async with SessionLocal() as db:
            from app.models import AiProject

            p = await db.get(AiProject, w["proj"].id)
            n2 = await _mk_novel(db, w["ua"].id, "甲书新卷")
            await _mk_job(db, p.id, w["ca"].id)
            p.novel_id = n2.id
            await db.commit()
            return p.id

    pid = asyncio.run(_rebind())
    resp2 = w["client"].get(f"/api/ai-factory/projects/{pid}/deai-report?chapter_id={w['ca'].id}")
    assert resp2.status_code == 200, f"② 换绑+job 证据的历史章被误杀（回归位）：{resp2.status_code} {resp2.text[:150]}"


def test_deai_report_still_rejects_cross_user(world):
    """ai_extras 收敛不许放松拦截面：别人的 chapter_id → 404，绝不带他人正文。"""
    w = world
    _as_user(w, w["ua"])
    resp = w["client"].get(f"/api/ai-factory/projects/{w['proj'].id}/deai-report?chapter_id={w['cb'].id}")
    assert resp.status_code == 404, f"跨用户 chapter_id 应 404，实得 {resp.status_code}"
    for marker in LEAK_MARKERS:
        assert marker not in resp.text


def test_toctou_legit_path_never_requests_after_attribution(world, monkeypatch):
    """归属对 → 不该发出任何上游请求（快速拒绝的正面证据 + TOCTOU 防护）。

    合法章 + 无 AIConfig 的正常形状：review 端点在 _pick_config 处 400（零网络）。
    把 _chat_text 打成哨兵异常——若执行流竟越过归属/配置闸发出请求，红在这里。
    这条同时是"端点层为什么可以快"的自证：归属正确路径同样不发网络。
    """
    from app.routers import ai_factory as fac

    w = world
    _as_user(w, w["ua"])

    async def _boom(*a, **k):
        raise AssertionError("归属校验之后不应发出任何上游请求（测试世界无可用配置，请求=泄漏旁路）")

    monkeypatch.setattr(fac, "_chat_text", _boom)
    jid = asyncio.run(_mk_own_job(w))
    resp = w["client"].post(f"/api/ai-factory/projects/{w['proj'].id}/jobs/{jid}/review")
    # 合法路径：不被归属挡（404），停在配置闸 400 —— 而不是 200（说明根本没干活）或 5xx
    assert resp.status_code == 400, f"合法章+无配置应 400（配置闸），实得 {resp.status_code}：{resp.text[:150]}"


async def _mk_own_job(world):
    from app.db import SessionLocal

    async with SessionLocal() as db:
        j = await _mk_job(db, world["proj"].id, world["ca"].id)
        return j.id


def test_shared_entry_is_single_source():
    """结构闸：泄露点位必须经共享入口，不许各自复制 if。

    判据：三个 router 文件里裸 `db.get(Chapter, job.chapter_id)` 取章数应为 0
    （取章挪进 deps.ensure_chapter_in_project 内部）；deps.py 里有该入口定义。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    total = 0
    for rel in ("routers/batch.py", "routers/ai_factory.py", "routers/ai_extras.py"):
        src = (root / rel).read_text(encoding="utf-8")
        total += src.count("db.get(Chapter, job.chapter_id)")
    assert total == 0, f"router 层仍有 {total} 处裸 db.get(Chapter, job.chapter_id)：判据又被复制了"
    deps_src = (root / "deps.py").read_text(encoding="utf-8")
    assert "def ensure_chapter_in_project" in deps_src, "共享入口缺失"


# ================================================================ u0a-tail：桩世界 A 腿攻击面（R1/R2）
#
# 🔴 本节直接调用 deps.ensure_chapter_in_project，**不走端点**。原因（复审 m5/m6/m8 三靶
# 皆 0 红的病根）：端点层在归属点之前还有 `p.novel_id is None → 400` 业务守卫与 job 查询，
# 替身 DB 端点测试根本轮不到桩腿的跨用户判定；而 `_has_chapter_job_stub` 无论怎么改
# （恒真/恒假/_hit 放宽）既有全套测试全绿——这条腿决定 fix6/7/8/wire 那批用替身 DB 的
# 端点的放行面，必须在这里裸钉。夹具全部手工构造 SimpleNamespace 行 + rows 字典桩
# （FakeDB/SeqDB 形状，见 _has_chapter_job_stub docstring 判定序第 1 条），零网络、零建库。

STUB_SECRET = "桩世界绝密正文：外人不可读。"


class _StubRowsDb:
    """rows 字典型表级桩（FakeDB/SeqDB/_BatchDB/wire_observe.SeqDB 同形状）。"""

    def __init__(self, chapters, jobs):
        from app.models import AiChapterJob as _ACJ
        from app.models import Chapter as _CH

        self.rows = {_CH: list(chapters), _ACJ: list(jobs)}

    async def get(self, model, pk):
        for r in self.rows.get(model, []):
            if getattr(r, "id", None) == pk:
                return r
        return None


def _stub_p(pid=1, uid=7, novel_id=None):
    # SimpleNamespace 无 __table__ → stub_p 分支激活（deps.py 以 hasattr(p,"__table__") 判形）
    return SimpleNamespace(id=pid, user_id=uid, novel_id=novel_id)


def _stub_chapter(cid, novel_id, content=STUB_SECRET):
    return SimpleNamespace(id=cid, novel_id=novel_id, title="桩章", content=content)


def _stub_job(jid, project_id, chapter_id):
    return SimpleNamespace(id=jid, project_id=project_id, chapter_id=chapter_id, status="pending")


def _stub_call(p, cid, db):
    from app.deps import ensure_chapter_in_project

    async def _go():
        return await ensure_chapter_in_project(p, cid, db)

    return asyncio.run(_go())


class TestStubLegAttacks:
    """R1：桩世界 A 腿（_has_chapter_job_stub 的 same_book ∧ _hit）攻击档。期望值硬编码。"""

    def test_stub_leg_rejects_foreign_project_job(self):
        """m8 形状防线：桩世界里放一条 project_id 属**他人**的 job 行——即使章缺 novel_id
        （same_book 护栏天然放行），job.project_id != p.id 也必须判否 → 404。
        变异"桩腿恒真"或"_hit 不比对 project_id"在此必红。"""
        from fastapi import HTTPException

        ch = _stub_chapter(21, None)                      # 老桩形状：章行缺 novel_id
        evil = _stub_job(99, 2, 21)                        # project_id=2 ≠ p.id=1（他人项目）
        db = _StubRowsDb([ch], [evil])
        with pytest.raises(HTTPException) as ei:
            _stub_call(_stub_p(1, novel_id=None), 21, db)
        assert ei.value.status_code == 404, f"他人 job 必须 404，实得 {ei.value.status_code}"

    def test_stub_leg_same_book_guard_blocks_other_book_chapter(self):
        """R1 护栏档：同项目有 job 证据，但那条 job 指向的是**另一本书**的章——
        请求本卷之外的章（novel_id 不同且无该章 job）必须被 same_book 护栏 + _hit
        粒度双重挡住 → 404。防"桩腿放宽成 or/存在任一 job 即放行"。"""
        from fastapi import HTTPException

        ch_own = _stub_chapter(21, 5)                      # 本项目书 N5 的章（同书护栏通过，但无该章 job）
        ch_other = _stub_chapter(22, 6)                    # 别的书 N6 的章：与 p 不同书
        job_for_22 = _stub_job(98, 1, 22)                  # 本项目 p=1 的 job，绑的却是 N6 章
        job_for_21 = _stub_job(95, 1, 21)                  # 本项目 p=1 的 job，绑本卷章 21（合法出路证据）
        db = _StubRowsDb([ch_own, ch_other], [job_for_21, job_for_22])
        p = _stub_p(1, novel_id=None)                      # p.novel_id=None → B 腿不成立，只能走桩 A 腿
        with pytest.raises(HTTPException) as ei:
            _stub_call(p, 21, db)                          # 请求 N5 章：无 job → 404
        assert ei.value.status_code == 404, f"无 job 的本卷章必须 404，实得 {ei.value.status_code}"
        with pytest.raises(HTTPException) as ei2:          # N6≠N5=p.novel_id → same_book=False，即使 job 两条件全中也必须 404
            _stub_call(p, 22, db)                          # （桩世界跨书防线：护栏在 _hit 之前把关）
        assert ei2.value.status_code == 404, f"同项目不同书的章必须被 same_book 护栏挡住，实得 {ei2.value.status_code}"

    def test_stub_leg_hit_requires_exact_chapter_id(self):
        """_hit 的 chapter_id 侧：同项目 job 绑章 21，不得顺带放行同书的章 22 → 404。"""
        from fastapi import HTTPException

        ch21, ch22 = _stub_chapter(21, 5), _stub_chapter(22, 5)
        db = _StubRowsDb([ch21, ch22], [_stub_job(97, 1, 21)])
        with pytest.raises(HTTPException) as ei:
            _stub_call(_stub_p(1, novel_id=None), 22, db)
        assert ei.value.status_code == 404, f"job 证据只钉那一条章，实得 {ei.value.status_code}"

    def test_db_get_exception_becomes_404_not_500(self):
        """R2（m10 形状防线）：替身 db.get 主动抛异常 → 入口必须转成 HTTPException(404)，
        不许裸异常冒成 500。摘掉 try/except 的变异在此必红。"""
        from fastapi import HTTPException

        class _BoomDb:
            async def get(self, *a, **k):
                raise RuntimeError("桩连 get() 都没备出")

        with pytest.raises(HTTPException) as ei:
            _stub_call(_stub_p(1, novel_id=5), 21, _BoomDb())
        assert ei.value.status_code == 404, (
            f"db.get 抛错必须保守拒绝成 404（非裸异常/500），实得 {ei.value.status_code}"
        )

    def test_falsy_chapter_id_never_reaches_stub_leg_or_db_get(self):
        """R3（m7b 形状绊马索）：chapter_id=0 时，桩 db.get 故意**盲返回不等主键的行**
        （复刻 ORM 投影退化形状）。若上游假值闸被改成 `is None`，0 会穿透并被盲桩喂回
        的行放行 → 本条变红。现在 `if not chapter_id` 必须在任何取章之前直接 404。"""
        from fastapi import HTTPException

        blind = _stub_chapter(0, 5)                        # 盲桩：get(任意pk)都吐这一行
        job0 = _stub_job(96, 1, 0)                         # orphan job 形状也一并备好（洞开时全链绿灯）

        class _BlindDb(_StubRowsDb):
            async def get(self, model, pk):
                return blind                               # 不等主键，照吐

        db = _BlindDb([blind], [job0])
        for cid in (0, None):
            with pytest.raises(HTTPException) as ei:
                _stub_call(_stub_p(1, novel_id=5), cid, db)
            assert ei.value.status_code == 404, f"chapter_id={cid!r} 必须在前置闸 404，实得 {ei.value.status_code}"
