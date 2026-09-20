"""失败文案与诊断解析的测试（纯逻辑，无网络）。

背景：用户报「点生成，过一会儿什么都没有」——排查发现两条路都是静默的：
- 流式（_stream_openai）：模型 200 但零增量时只发 done，不发 error
- 非流式（_chat_text）：content 为空直接返回空字符串
于是前端「无错也无字」，任务还卡在 writing 直到 15 分钟自动解锁。
这里钉住的是「空内容必须报出配置/模型/端点」这条契约，以及它能被正确归类。

运行：PYTHONPATH=. pytest tests/test_failure_messages.py -q
"""

import asyncio

from app.failure_kinds import classify_failure, empty_output_error
from app.models import AIConfig, User
from app.routers.ai_factory import _effective_route


class TestEmptyOutputMessage:
    def test_contains_actionable_identifiers(self):
        """文案必须带配置名/模型名/端点——这是用户唯一能改的三样东西。"""
        msg = empty_output_error("天枢", "deepseek-reasoner", "https://api.example.com", stream=True)
        assert "天枢" in msg
        assert "deepseek-reasoner" in msg
        assert "https://api.example.com" in msg

    def test_mentions_channel(self):
        """流式/非流式要能区分：不同通道的排查方式不一样。"""
        assert "流式" in empty_output_error("A", "m", "u", stream=True)
        assert "非流式" in empty_output_error("A", "m", "u", stream=False)

    def test_classifies_as_empty_output(self):
        """归类到 empty_output，用户才能拿到配套建议（换模型重试）。"""
        info = classify_failure(empty_output_error("天枢", "m", "u", stream=True))
        assert info.code == "empty_output"
        assert info.hint  # 必须给出怎么办

    def test_does_not_misclassify_as_other_kinds(self):
        """文案里出现「端点/模型名」等词，不能被误判成网络/模型不存在等别的原因。"""
        msg = empty_output_error("配置A", "gpt-x", "https://api.example.com/v1", stream=False)
        code = classify_failure(msg).code
        assert code == "empty_output", f"被误判为 {code}"

    def test_no_format_placeholder_left(self):
        """用 f-string 拼接，不能把 {} 留成占位符（曾用 % 拼接踩过）。"""
        msg = empty_output_error("{name}", "{model}", "{url}", stream=True)
        assert "{name}" in msg  # 用户真起了这种名字也要原样显示
        assert "%s" not in msg


class _FakeResult:
    def __init__(self, config):
        self._config = config

    def scalars(self):
        return self

    def first(self):
        return self._config


class _FakeDB:
    """只实现 _pick_config 用到的 db.get(AIConfig, id)。"""

    def __init__(self, config=None):
        self._config = config

    async def get(self, model, pk):
        if model is AIConfig and self._config is not None and self._config.id == pk:
            return self._config
        return None

    async def execute(self, *_a, **_kw):
        # 回退到「默认配置」走的是 select(...).scalars().first()，这里照实返回，
        # 否则测不到「路由指向已删配置 → 静默回退到默认配置」这条真实路径。
        return _FakeResult(self._config)


def _user(uid=1):
    return User(id=uid, username="t", password_hash="!", role="author")


def _config(cid=3, uid=1, name="天枢", model="deepseek-chat", key="sk-x"):
    return AIConfig(id=cid, user_id=uid, name=name, base_url="https://api.example.com", api_key=key, model=model)


class TestEffectiveRoute:
    """诊断里必须能看出「路由实际解析成哪个模型」——包括静默回退到默认的实情。"""

    def test_resolves_routed_config_and_model(self):
        cfg = _config()
        out = asyncio.run(_effective_route(_user(), _FakeDB(cfg), "3@deepseek-reasoner"))
        assert out["resolved"] == "天枢 / deepseek-reasoner"
        assert out["base_url"] == "https://api.example.com"
        assert out["problem"] == ""

    def test_reports_requested_value(self):
        out = asyncio.run(_effective_route(_user(), _FakeDB(_config()), "3@m"))
        assert out["requested"] == "3@m"

    def test_missing_config_falls_back_to_default_without_raising(self):
        """路由指向已删除的配置：不能抛错（诊断本身不该 500），且要如实报出实际用的。"""
        default = _config(cid=9, name="默认", model="deepseek-chat")
        db = _FakeDB(default)
        out = asyncio.run(_effective_route(_user(), db, "999@ghost"))
        assert out["resolved"] == "默认 / deepseek-chat"  # 静默回退的实情

    def test_no_config_at_all_reports_problem(self):
        """一个配置都没有：给出 problem，界面据此提示「这就是生成不出内容的原因」。"""
        out = asyncio.run(_effective_route(_user(), _FakeDB(None), None))
        assert out["problem"]
        assert out["resolved"] == ""

    def test_empty_route_marks_follow_default(self):
        out = asyncio.run(_effective_route(_user(), _FakeDB(_config()), None))
        assert out["requested"] == "(跟随默认配置)"

    def test_config_without_key_is_reported(self):
        """没填 Key 的配置不能用，_pick_config 会回退；诊断要显示实际结果而不是崩溃。"""
        keyless = _config(cid=5, name="空Key", key="")
        out = asyncio.run(_effective_route(_user(), _FakeDB(keyless), "5@m"))
        assert out["resolved"] == "" and out["problem"]
