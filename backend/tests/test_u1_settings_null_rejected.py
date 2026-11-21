"""u1-fix2：NOT NULL 设置字段（当前 11 个）显式 null 一律被拒（422 形状）。

背景（实测口径）：`PRAGMA table_info(ai_projects)` 实测下列列 notnull=1（u5 起为 11 列，含 context_extra_chapters），
与 `models.py` 的非 Optional 类型标注一致（Mapped[bool]/Mapped[str]/Mapped[int] ⇒ nullable=False）：

  auto_mode(BOOLEAN) / author_intent(TEXT) / current_focus(TEXT) / kb_query(VARCHAR(200)) /
  auto_kb_sync(BOOLEAN) / platform(VARCHAR(20)) / custom_words(VARCHAR(500)) /
  nightly_enabled(BOOLEAN) / nightly_chapters(INTEGER) / context_recent_chapters(INTEGER)

而 `ProjectSettingsIn` 里这些字段的类型都允许 `None`，`update_project` 又走
`model_dump(exclude_unset=True)` + 通用 `setattr` —— 所以**显式**传 null 会把 None
塞进 NOT NULL 列 → sqlite `IntegrityError: NOT NULL constraint failed` → HTTP 500。

本文件钉住三件事（期望值全部硬编码，不从实现 import 任何常量）：
  ① 这些字段各自显式传 None，必须在 pydantic 构造层被拒（FastAPI 对请求体校验失败即 422），
     且错误 message/loc 点名该字段——不能是"别的字段先炸了"的假通过；
  ② 反向对拍：**键不出现**时构造必须成功，且 `model_dump(exclude_unset=True)` 不含它们
     （这条防把 `exclude_unset` 语义改坏，/把"堵 null"变成"全字段必填"）；
  ③ 端到端代表字段：先设 platform="起点" → 传 platform=None 被拒 → 真查库仍是 "起点"
     （证明拒绝发生在落库之前，库里没被改动）。

⚠️ 失败形状必须能区分（核心）：
   - 正确：`ValidationError`，`errors()[0]["type"] == "value_error"` —— 校验拒绝 → 422；
   - 错误（改前）：构造直接通过 → 落到 commit 时 `IntegrityError` → 500；
   - 错误（把 null 静默 ignore）：构造通过 → 调用方以为清空成功、实际没清，且本文件的 ①③ 红。
 所以 ① 里断言的是 `value_error` 这个类型，IntegrityError 永远满足不了它。
"""

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.ai_factory as factory
from app.models import AiProject, Base, User

# 「PRAGMA table_info(ai_projects) 实测 notnull=1」的那批列，按字段名硬编码（不从实现 import）
NOT_NULL_FIELDS = (
    "auto_mode",
    "author_intent",
    "current_focus",
    "kb_query",
    "auto_kb_sync",
    "platform",
    "custom_words",
    "nightly_enabled",
    "nightly_chapters",
    "context_recent_chapters",
    "context_extra_chapters",
)

# 同一批字段的合法值：用来证明校验不是"一刀切全拒"（正对照，防止过度拦截）
VALID_VALUES = {
    "auto_mode": True,
    "author_intent": "少年逆袭，不写后宫",
    "current_focus": "第三卷收网",
    "kb_query": "剑仙 设定",
    "auto_kb_sync": False,
    "platform": "起点",
    "custom_words": "雷区,地雷",
    "nightly_enabled": True,
    "nightly_chapters": 3,
    "context_recent_chapters": 2,
    "context_extra_chapters": [3, 5],
}


@pytest_asyncio.fixture
async def sl():
    """独立内存库：create_all 直接按 models.py 建表（含 NOT NULL 约束）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _make_project(sl: async_sessionmaker) -> tuple[User, AiProject]:
    async with sl() as db:
        user = User(username="u1fix2-tester", password_hash="x")
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


async def _read_platform(sl, pid) -> str:
    """新 session 真查库，不读调用方内存里的对象。"""
    async with sl() as db:
        fresh = await db.get(AiProject, pid)
        return fresh.platform


# ---------- ① 每个字段逐个显式传 None：必须在 pydantic 层被拒 ----------


@pytest.mark.parametrize("field", NOT_NULL_FIELDS)
def test_explicit_null_rejected_for_each_field(field):
    """每个字段单独一个用例：传 None 一律 ValidationError，且点名该字段。"""
    with pytest.raises(ValidationError) as ei:
        factory.ProjectSettingsIn(**{field: None})

    err = ei.value
    first = err.errors()[0]
    # 必须是"校验拒绝"（value_error），绝不是落库时冒出来的 IntegrityError
    assert first["type"] == "value_error", (
        f"{field}: 拒绝方式不是 pydantic 校验错误而是 {first['type']}，"
        f"说明 null 越过了 schema 层（真到 DB 就是 IntegrityError → 500）：{err}"
    )
    # model 级校验器（mode="before"）的 loc 是空元组，字段名一定在 msg 里：两者取其一
    assert field in first["msg"] or field in first["loc"], f"{field}: 错误没点名该字段（msg={first['msg']!r} loc={first['loc']}）"
    assert field in str(err), f"{field}: 错误信息里没点名该字段：{err}"
    # 提示口径：告诉调用方该怎么清空（不能只说"不许 null"）
    assert "不允许空" in str(err), f"{field}: 没说明该列不允许空：{err}"
    assert "空串" in str(err) and "false" in str(err), f"{field}: 没给出替代写法（空串/0/false）：{err}"


# ---------- ② 键不出现 = 一律放行，且 exclude_unset 不含这些键（反向对拍） ----------


def test_omitted_keys_still_allowed_and_excluded_from_dump():
    """只传一个可空字段：NOT NULL 字段的键都没出现，构造必须成功且 dump 不含它们。

    这条防的是"堵 null 顺手把 exclude_unset 语义改坏"——一旦改成给这些字段设必填/设
    非 None 默认值，全量 PUT 会把用户没动的列一起冲掉。
    """
    m = factory.ProjectSettingsIn(target_chapter_words=2500)
    dumped = m.model_dump(exclude_unset=True)
    assert dumped == {"target_chapter_words": 2500}, f"exclude_unset 语义被破坏：{dumped}"
    for field in NOT_NULL_FIELDS:
        assert field not in dumped, f"{field} 没出现在请求里却被 dump 出来 → 会被通用 setattr 覆盖"


def test_empty_request_is_valid():
    """空请求体 {} 仍然合法（所有字段皆可选）。"""
    assert factory.ProjectSettingsIn().model_dump(exclude_unset=True) == {}


# ---------- 正对照：合法值必须照常通过并被 dump（防一刀切全拒） ----------


@pytest.mark.parametrize("field", NOT_NULL_FIELDS)
def test_valid_value_accepted_for_each_field(field):
    value = VALID_VALUES[field]
    m = factory.ProjectSettingsIn(**{field: value})
    assert m.model_dump(exclude_unset=True) == {field: value}, f"{field}: 合法值被误拦或语义变了"


# ---------- ③ 端到端代表字段：被拒之后库里还是原值 ----------


@pytest.mark.asyncio
async def test_null_platform_rejected_and_db_unchanged(sl):
    """先设 platform="起点" → 传 platform=None 被拒 → 真查库仍是 "起点"。

    改前形状：schema 收下 None → update_project 通用 setattr → NOT NULL 列
    → IntegrityError → HTTP 500（所以这里的"没被拒"分支直接 fail 并写明因果链，
    而不是让 IntegrityError 冒出来被读成"测试环境坏了"）。
    """
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"platform": "起点"})
    assert await _read_platform(sl, p.id) == "起点", "前置条件失败：合法值没落库"

    try:
        factory.ProjectSettingsIn(**{"platform": None})
    except ValidationError as e:
        assert "platform" in str(e), f"拒绝了但没点名该字段：{e}"
    else:
        pytest.fail(
            "schema 接受了 platform=None：显式 null 会一路走到 update_project 的通用 setattr，"
            "把 None 塞进 platform VARCHAR(20) NOT NULL 列 → sqlite IntegrityError → HTTP 500。"
            "该 null 必须在 pydantic 构造层就被挡（=422 形状）。"
        )

    # 反向对拍：被拒的请求不得改库
    assert await _read_platform(sl, p.id) == "起点", "请求被拒了库却被改，说明拒绝发生在落库之后"


@pytest.mark.asyncio
async def test_null_platform_never_reaches_update_project(sl):
    """整条 PUT 路径：传 null 的 payload 根本构造不出来 → update_project 不会被调用 → 库不动。

    改前这一调用会抛 IntegrityError（500 的真相），所以这里用 ValidationError 断言把
    "被校验拒绝" 与 "落库崩了" 分开。
    """
    user, p = await _make_project(sl)
    await _put(sl, user, p.id, {"platform": "起点"})

    with pytest.raises(ValidationError):
        await _put(sl, user, p.id, {"platform": None})

    assert await _read_platform(sl, p.id) == "起点"
