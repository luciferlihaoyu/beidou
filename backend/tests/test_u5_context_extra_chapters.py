"""u5 项二：context_extra_chapters 显式 null 一律 422（与另外 10 个 NOT NULL 字段同口径）。

实测依据（本单实读）：
- `models.py:369` `context_extra_chapters: Mapped[str] = mapped_column(Text, default="[]")`
  —— `Mapped[str]` 非 Optional ⇒ nullable=False（NOT NULL），与 u1-fix2 那 10 个字段同形。
- 该字段此前**不在** `_NOT_NULL_SETTINGS_FIELDS` 里，而 `update_project` 对它另有特判
  `setattr(p, field, json.dumps(value or []))` ⇒ 显式传 null 会被**静默**写成 "[]"：
  不报错、不 500、用户已配置的扩展章节被无声抹掉（比 500 更难发现——没有任何痕迹）。

本文件钉住四件事（期望值硬编码，不从实现 import 任何常量）：
① None → pydantic 层 ValidationError(value_error) 且点名该字段（FastAPI 回 422），
   **不是**静默清空、**也不是**落库时的 IntegrityError；
② [] → 正常写库为 "[]"（**清空的正确姿势**，`value or []` 特判保留不动）；
③ [3, 5] → 正常写库，json 回读等于 [3, 5]；
④ 键不出现 → exclude_unset 里没有该键，且落库值一字不动（不传=不改；防"堵 null"顺手
   把字段改成必填／塞非 None 默认值，从而把用户没动的列一起冲掉）。
"""

import json

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.ai_factory as factory
from app.models import AiProject, Base, User


@pytest_asyncio.fixture
async def sl():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_project(sl):
    async with sl() as db:
        user = User(username="u5-ctx-tester", password_hash="x")
        db.add(user)
        await db.flush()
        project = AiProject(user_id=user.id, seed_prompt="少年逆袭成剑仙")
        db.add(project)
        await db.commit()
        return user, project


async def _put(sl, user, pid, payload: dict):
    """与前端 PUT 同一条路径（只跳过 HTTP 层）。"""
    async with sl() as db:
        return await factory.update_project(pid, factory.ProjectSettingsIn(**payload), user, db)


async def _read_extra(sl, pid):
    """新 session 真查库，不读调用方内存里的对象。"""
    async with sl() as db:
        return (await db.get(AiProject, pid)).context_extra_chapters


# ---------- ① 显式 null：必须在 pydantic 层被拒（422），不许静默清空 ----------


def test_explicit_null_rejected_at_schema_layer():
    with pytest.raises(ValidationError) as ei:
        factory.ProjectSettingsIn(context_extra_chapters=None)

    first = ei.value.errors()[0]
    assert first["type"] == "value_error", (
        "拒绝方式不是 pydantic 校验错误而是 %r：null 越过了 schema 层"
        "（改前它会被 json.dumps(None or []) 静默写成 \"[]\"）：%s" % (first["type"], ei.value)
    )
    assert "context_extra_chapters" in (first["msg"] + str(first["loc"])), "错误没点名该字段：%r" % (first,)
    assert "不允许空" in str(ei.value), "没说明该列不允许空：%s" % (ei.value,)


# ---------- ② 清空的正确姿势：[] ----------


@pytest.mark.asyncio
async def test_empty_list_clears_to_empty_json(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"context_extra_chapters": [3, 5]})
    assert await _read_extra(sl, p.id) == json.dumps([3, 5]), "前置条件不成立：先写没生效"

    await _put(sl, user, p.id, {"context_extra_chapters": []})
    assert await _read_extra(sl, p.id) == "[]", "传 [] 没有写成 \"[]\"：特判 value or [] 被改坏了"


# ---------- ③ 正常值：照常落库 ----------


@pytest.mark.asyncio
async def test_id_list_written_as_json(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"context_extra_chapters": [3, 5]})
    raw = await _read_extra(sl, p.id)
    assert raw == "[3, 5]", "落库形态变了：%r" % (raw,)
    assert json.loads(raw) == [3, 5], "回读不是 [3, 5]：%r" % (raw,)


# ---------- ④ 键不出现：dump 不含该键，且库里一字不动 ----------


def test_omitted_key_absent_from_exclude_unset_dump():
    dumped = factory.ProjectSettingsIn(target_chapter_words=2500).model_dump(exclude_unset=True)
    assert dumped == {"target_chapter_words": 2500}, "exclude_unset 语义被破坏：%r" % (dumped,)
    assert "context_extra_chapters" not in dumped, "没传的字段被 dump 出来 → 会被通用 setattr 覆盖"


@pytest.mark.asyncio
async def test_omitted_key_keeps_stored_value(sl):
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"context_extra_chapters": [3, 5]})
    await _put(sl, user, p.id, {"target_chapter_words": 2500})
    assert await _read_extra(sl, p.id) == json.dumps([3, 5]), "只改字数却把扩展章节配置冲掉了"
