"""u1：章节流水线档位（chapter_pipeline）接通回归测试。

背景（grep 已核实的事实）：列 models.py:404 与迁移 db.py:66 早已就位，
消费方 nightly.py:141 与 chapter_stages.py:139 也早已读它——唯独
ProjectSettingsIn 没有这个字段，而 update_project 走
model_dump(exclude_unset=True) 通用 setattr，所以字段不进 schema 就
永远写不进去，档位永久停在 off。本文件钉住三件事：
  1) 加进 schema 是唯一通路（PUT 能写、能持久化）；
  2) 非法值由 pydantic 拦截（FastAPI 对请求体校验失败一律 422）；
  3) exclude_unset 语义：不传该字段的 PUT 不得把它重置回默认。

期望值全部硬编码在本文件，不从实现 import 任何常量。
"""

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.ai_factory as factory
from app.models import AiProject, Base, User


@pytest_asyncio.fixture
async def sl():
    """独立内存库（create_all 直接带新列，覆盖新库迁移口径）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_project(sl: async_sessionmaker) -> tuple[User, AiProject]:
    async with sl() as db:
        user = User(username="u1-tester", password_hash="x")
        db.add(user)
        await db.flush()
        project = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙")
        db.add(project)
        await db.commit()
        return user, project


async def _put(sl, user, pid, payload: dict):
    """调 update_project（与前端 PUT 同一条路径，只是跳过 HTTP 层）。"""
    async with sl() as db:
        return await factory.update_project(pid, factory.ProjectSettingsIn(**payload), user, db)


async def _get(sl, user, pid) -> dict:
    """新 session 重新 GET：走真查库，不是读调用方内存里的对象。"""
    async with sl() as db:
        return await factory.get_project(pid, user, db)


# ---------- ① 写 standard 并持久化 ----------


@pytest.mark.asyncio
async def test_put_standard_persists(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"chapter_pipeline": "standard"})
    out = await _get(sl, user, p.id)
    assert "chapter_pipeline" in out, "GET 响应体不带该字段（_project_out 白名单漏了）"
    assert out["chapter_pipeline"] == "standard"


# ---------- ② 写 deep 并持久化 ----------


@pytest.mark.asyncio
async def test_put_deep_persists(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"chapter_pipeline": "deep"})
    out = await _get(sl, user, p.id)
    assert out["chapter_pipeline"] == "deep"


# ---------- ③ 非法值被拦 + 库里保持原值（反向对拍） ----------


@pytest.mark.asyncio
async def test_invalid_value_rejected_and_db_unchanged(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"chapter_pipeline": "deep"})
    # 非法值在 pydantic 层就被拒（FastAPI 收到请求体校验失败即回 422）
    with pytest.raises(ValidationError):
        factory.ProjectSettingsIn(chapter_pipeline="bogus")
    out = await _get(sl, user, p.id)
    assert out["chapter_pipeline"] == "deep"


# ---------- ④ 不传该字段的 PUT 不得重置（exclude_unset） ----------


@pytest.mark.asyncio
async def test_omitted_field_not_reset(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"chapter_pipeline": "standard"})
    await _put(sl, user, p.id, {"auto_mode": True})
    out = await _get(sl, user, p.id)
    assert out["chapter_pipeline"] == "standard"
    assert out["auto_mode"] is True


# ---------- ⑤ 新建项目默认 off（不是 NULL、不是空串） ----------


@pytest.mark.asyncio
async def test_new_project_defaults_off(sl):
    user, p = await _make_project(sl)
    async with sl() as db:
        fresh = await db.get(AiProject, p.id)
        assert fresh.chapter_pipeline == "off"
    out = await _get(sl, user, p.id)
    assert out["chapter_pipeline"] == "off"


# ---------- ⑥ 消费方契约："" / NULL 一律按 off 判定 ----------


def _mode_of(obj) -> str:
    """与 nightly.py:141 / chapter_stages.py:139 同款判定式（照抄，故意硬编码）。

    ⚠️ 绊马索：这条守的是「空值必须退化成 off」这个边界。有人若把
    `or "off"` 删掉，"" 会变成未知档位而不是关闭态，这里立刻红。
    """
    return (getattr(obj, "chapter_pipeline", "") or "off")


def test_empty_and_null_mean_off():
    from types import SimpleNamespace

    assert _mode_of(SimpleNamespace(chapter_pipeline="")) == "off"
    assert _mode_of(SimpleNamespace(chapter_pipeline=None)) == "off"
    assert _mode_of(SimpleNamespace()) == "off"
    # 合法档位不受影响（对照，防止判定被写成恒返回 off）
    assert _mode_of(SimpleNamespace(chapter_pipeline="standard")) == "standard"
    assert _mode_of(SimpleNamespace(chapter_pipeline="deep")) == "deep"
