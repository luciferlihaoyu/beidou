"""P0 安全回归：资料库搜索（GET /api/library/search）跨用户数据泄露（IDOR）。

被钉死的漏洞：不传 novel_id 时 _check_scope 直接放行，search_library_fts 据此
不加任何 scope 条件做 FTS 全表 MATCH，命中后 _item_out 返回完整 content——
任何登录用户用一个常见检索词就能遍历所有人的小说专属资料库全文。

修复契约：novel_id=None 时只返回「公共库（novel_id IS NULL）+ 当前用户自己
拥有的小说专属库」，他人小说 scope 的命中必须在路由层被丢弃（含 snippet）。

测试模式沿用 tests/test_chapter_status.py 的 E2E 口径：临时文件 SQLite +
dependency_overrides[get_db] + httpx.ASGITransport 打真实路由；认证走真实链路
（create_token 签发 JWT、不替换 get_current_user），因为漏洞本身就是「用谁的
token 在搜」。条目一律经真实端点 POST /api/library/items 创建，顺带验证
FTS 同步钩子（sync_library_item）确实生效。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_library_search_scope.py -q
"""

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.deps import get_db
from app.models import Base, Novel, User
from app.search_fts import ensure_fts
from app.security import create_token

# 各条目独有的检索词（纯中文 → FTS phrase query；词面互不包含，杜绝交叉命中）。
# ⚠ 分词硬约束（实测钉死）：unicode61 把连续 CJK 串当**单个 token**，短语必须
# 恰好对齐完整 token 串才命中——所以检索词一律放 content 句首、后接全角标点，
# 否则 FTS 根本搜不到（泄露用例会被「假绿」掩盖）。
SECRET_A = "天罡剑诀密档"      # 只出现在用户 A 的小说专属库
PUBLIC_MARK = "公共灵材图鉴"   # 只出现在公共库条目
OWN_B = "碧水潭设定稿"         # 只出现在用户 B 自己的小说专属库


@pytest_asyncio.fixture
async def env(tmp_path):
    """两名普通用户（A/B）+ 各一本小说 + 真实 token + 接临时库的 ASGI 客户端。"""
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_fts(conn)  # FTS5 虚拟表（library_items_fts），资料搜索依赖
    SL = async_sessionmaker(engine, expire_on_commit=False)

    async with SL() as s:
        user_a = User(username="scope_user_a", password_hash="x", role="author")
        user_b = User(username="scope_user_b", password_hash="x", role="author")
        s.add_all([user_a, user_b])
        await s.flush()
        novel_a = Novel(user_id=user_a.id, title="A之书", author="a", description="", genre="")
        novel_b = Novel(user_id=user_b.id, title="B之书", author="b", description="", genre="")
        s.add_all([novel_a, novel_b])
        await s.commit()
        ids = SimpleNamespace(
            user_a=user_a.id, user_b=user_b.id, novel_a=novel_a.id, novel_b=novel_b.id
        )

    async def fake_get_db():
        async with SL() as s:
            yield s

    app.dependency_overrides[get_db] = fake_get_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def create_item(token: str, payload: dict) -> dict:
        """走真实端点建资料条目（触发 FTS 同步钩子）。"""
        r = await client.post(
            "/api/library/items", json=payload, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200, f"建条目失败 {r.status_code}: {r.text}"
        return r.json()

    async def search(token: str, q: str, novel_id: int | None = None):
        params: dict = {"q": q}
        if novel_id is not None:
            params["novel_id"] = novel_id
        return await client.get(
            "/api/library/search", params=params, headers={"Authorization": f"Bearer {token}"}
        )

    try:
        yield SimpleNamespace(
            ids=ids,
            token_a=create_token(ids.user_a, "scope_user_a"),
            token_b=create_token(ids.user_b, "scope_user_b"),
            create_item=create_item,
            search=search,
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
        await engine.dispose()  # 关键：防 aiosqlite worker 跨 loop 崩（test_chapter_status 同口径）


# ---------------------------------------------------------------- 核心泄露用例


@pytest.mark.asyncio
async def test_user_b_cannot_search_user_a_private_items(env):
    """P0 主钉：B 不传 novel_id 搜 A 专属库独有的词，绝不能命中 A 的任何资料。

    修复前现场：FTS 全表 MATCH → A 的条目带完整 content 返回给 B。
    """
    item_a = await env.create_item(
        env.token_a,
        {
            "novel_id": env.ids.novel_a,
            "title": "A的密档",
            "content": f"{SECRET_A}：此页外人不可见。",
        },
    )

    r = await env.search(env.token_b, SECRET_A)
    assert r.status_code == 200, r.text
    data = r.json()
    ids = {row["id"] for row in data}
    assert item_a["id"] not in ids, "跨用户泄露：B 不带 novel_id 命中了 A 的小说专属库条目"
    # 双保险：整份响应正文（含 snippet/content 字段）都不许出现 A 的资料内容
    assert SECRET_A not in r.text, "泄露面不止 id：snippet/content 把 A 的资料正文带出去了"


@pytest.mark.asyncio
async def test_user_b_can_search_public_items(env):
    """防过滤过度：公共库（novel_id IS NULL）条目对所有人可见，B 不传 novel_id 必须命中。"""
    pub = await env.create_item(
        env.token_a,  # 谁建的都一样，公共库全用户共享
        {"novel_id": None, "title": "灵材图鉴", "content": f"{PUBLIC_MARK}：千年龙涎草。"},
    )

    r = await env.search(env.token_b, PUBLIC_MARK)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert pub["id"] in ids, "公共库条目必须对 B 可见（修复不许把公共库一起滤掉）"


@pytest.mark.asyncio
async def test_user_b_can_search_own_items(env):
    """防过滤过度：B 不传 novel_id 也必须能搜到自己小说专属库的资料。"""
    item_b = await env.create_item(
        env.token_b,
        {"novel_id": env.ids.novel_b, "title": "B的设定", "content": f"{OWN_B}：潭底有古阵。"},
    )

    r = await env.search(env.token_b, OWN_B)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert item_b["id"] in ids, "自己小说专属库的条目必须仍被命中（修复不许一刀切成只搜公共库）"


# ---------------------------------------------------------------- 混合与回归护栏


@pytest.mark.asyncio
async def test_mixed_hits_keep_only_public_and_own(env):
    """混合场景：同一检索词同时命中 A专属/公共/B专属 三条，B 不带 novel_id
    只应拿到后两类。这是对「按 scope 集合过滤」最强的单条断言。"""
    shared = "混检灵纹关键词"
    a_item = await env.create_item(
        env.token_a, {"novel_id": env.ids.novel_a, "title": "A混检", "content": f"{shared}：A的私藏。"}
    )
    pub_item = await env.create_item(
        env.token_a, {"novel_id": None, "title": "公共混检", "content": f"{shared}：公共的存档。"}
    )
    b_item = await env.create_item(
        env.token_b, {"novel_id": env.ids.novel_b, "title": "B混检", "content": f"{shared}：B的笔记。"}
    )

    r = await env.search(env.token_b, shared)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert a_item["id"] not in ids, "他人小说 scope 的命中必须被丢弃"
    assert {pub_item["id"], b_item["id"]} <= ids, "公共库与本人小说的命中必须保留"


@pytest.mark.asyncio
async def test_scoped_search_by_foreign_novel_is_404(env):
    """B 带 A 的 novel_id 搜索 → 404：既有的归属校验不许被本次修复放松。"""
    r = await env.search(env.token_b, SECRET_A, novel_id=env.ids.novel_a)
    assert r.status_code == 404, f"越权带他人 novel_id 应 404，实得 {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_scoped_search_returns_own_and_public_only(env):
    """A 带自己 novel_id 搜索：命中「本人专属 + 公共」，不含 B 专属（既有语义回归钉）。"""
    shared = "定检青鸾关键词"
    a_item = await env.create_item(
        env.token_a, {"novel_id": env.ids.novel_a, "title": "A定检", "content": f"{shared}：A的定检。"}
    )
    pub_item = await env.create_item(
        env.token_a, {"novel_id": None, "title": "公共定检", "content": f"{shared}：公共定检。"}
    )
    b_item = await env.create_item(
        env.token_b, {"novel_id": env.ids.novel_b, "title": "B定检", "content": f"{shared}：B的定检。"}
    )

    r = await env.search(env.token_a, shared, novel_id=env.ids.novel_a)
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert b_item["id"] not in ids, "带 novel_id 时他人 scope 本就该被 FTS 条件滤掉"
    assert {a_item["id"], pub_item["id"]} <= ids, "带 novel_id 的「本书 + 公共」语义不许变"
