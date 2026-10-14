"""立项保留路径的书名守卫（加固批评审 minor-4）。

``init_project`` 重新立项时把作者已填的字段塞回 prompt：原实现走
``str(titles_in[0]).strip()``，``titles=[None]`` 会得到字面量 ``"None"`` —— 既写进
prompt 的「书名已定为「None」」（诱导模型沿用），又经「双保险」合并被强制塞进
``spec["titles"][0]``。脏值从**源头**进池，下游 ``_book_title`` 只能跳过兜底、
清不掉已经落库的候选。本文件直接调路由函数（假 LLM、假配置）钉住：非字符串首项
一律不采纳，书名回归由下游 ``_book_title`` 统一兜底。
"""

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.ai_factory as factory
from app.models import AiProject, Base, User


@pytest_asyncio.fixture
async def sl():
    """独立内存库（create_all 口径）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


#: AI 侧固定产出（titles 刻意都与作者已填的「备选名」不同，才看得出合并有没有被污染）
AI_JSON = json.dumps(
    {
        "titles": ["AI 候选一", "AI 候选二", "AI 候选三"],
        "genre": "玄幻",
        "time": "架空",
        "place": "青萍山",
        "theme": "逆袭",
        "tone": "热血",
        "pov": "第三人称限制视角-主角",
        "characters": [{"name": "林青云", "role": "主角", "brief": "外门弟子"}],
        "premise": "少年携剑逆袭。",
    },
    ensure_ascii=False,
)


@pytest.mark.parametrize("titles_in", [[None, "备选名"], [None], [123], [None, None]])
@pytest.mark.asyncio
async def test_init_project_never_writes_none_title(sl, monkeypatch, titles_in):
    """非串首项（None / 数字）不进 prompt、不进候选首位；字符串首项仍照旧保留（见下一例）。"""
    prompts: list[str] = []

    async def fake_chat(config, system, prompt):
        prompts.append(prompt)
        return AI_JSON

    async def fake_pick(user, db, route_field):
        return object()

    monkeypatch.setattr(factory, "_chat_text", fake_chat)
    monkeypatch.setattr(factory, "_pick_config", fake_pick)

    async with sl() as db:
        user = User(username="init-title", password_hash="x")
        db.add(user)
        await db.flush()
        p = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙", status="draft")
        db.add(p)
        await db.commit()

        out = await factory.init_project(
            p.id, factory.InitIn(existing_spec={"titles": titles_in}), user, db
        )

    assert "「None」" not in prompts[0], "非串首项不许在 prompt 里写成「书名已定为「None」」"
    assert out["book_spec"]["titles"][0] == "AI 候选一", "脏书名不许被强制合并到候选首位"
    assert out["book_spec"]["titles"] == ["AI 候选一", "AI 候选二", "AI 候选三"]


@pytest.mark.asyncio
async def test_init_project_still_keeps_string_title_first(sl, monkeypatch):
    """回归面：字符串首项照旧「放候选首位」——守卫只挡非串，不许把正常保留路径一并关掉。"""
    prompts: list[str] = []

    async def fake_chat(config, system, prompt):
        prompts.append(prompt)
        return AI_JSON

    async def fake_pick(user, db, route_field):
        return object()

    monkeypatch.setattr(factory, "_chat_text", fake_chat)
    monkeypatch.setattr(factory, "_pick_config", fake_pick)

    async with sl() as db:
        user = User(username="init-title-ok", password_hash="x")
        db.add(user)
        await db.flush()
        p = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙", status="draft")
        db.add(p)
        await db.commit()

        out = await factory.init_project(
            p.id, factory.InitIn(existing_spec={"titles": [" 备选名 ", None]}), user, db
        )

    assert "书名已定为「备选名」" in prompts[0], "字符串首项 strip 后照旧写进 prompt"
    assert out["book_spec"]["titles"][0] == "备选名", "作者定的书名照旧占候选首位"
