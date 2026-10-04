"""FIX-8 字数驱动轮（A/B/C）：单文件、零真实网络、不碰真实数据库、<30 秒。

用户原始诉求（本文件钉死的行为契约）：
- ③「章节字数是最低线，只能上浮，不能下降」：
    A1 生成 prompt 硬化——「不少于 X 字（硬下限），建议 X～1.3X」，告别 ±20% 浮动；
    A2 生成后补足——非流式路径（夜跑/批量）用 _ensure_min_words 续写一轮拼接；
       UI 流式路径用 _topup_stream 把续写内容帧透传进同一条 SSE（帧形状不变）。
- ②「4000 多字的章节明显超设定」：
    B  拆分逻辑挪到共享模块 app/chapter_split.py，夜跑/批量/finalize 三条路径统一
       调用；此前 finalize 不拆，是超长章漏进书稿的唯一通道。
- ①「设定章节字数+总字数 → 自动得出章数」：
    C  derive_target_chapters 纯函数 + _project_out 携带 derived_target_chapters +
       首次大纲 prompt 讲清「本书计划约 N 章，本次先出第一卷（≤30 章）」。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python3 -m pytest tests/test_fix8_wordcount.py -q
"""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.models import AiChapterJob, AiProject, Chapter, Novel, Volume
from app.routers import ai as ai_mod
from app.routers import ai_factory as factory_mod

TARGET = 2000


# ---------------------------------------------------------------- 通用替身


def fake_user():
    return SimpleNamespace(id=1, role="user")


def make_ai_config():
    from app.models import AIConfig

    return AIConfig(
        id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
    )


class _Rows:
    """db.execute(...) 结果的最小形态：.scalars().all()。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class SeqDB:
    """按调用顺序吐 execute 结果的最小 DB；get/commit/add/flush 均可用。"""

    def __init__(self, rows=None, results=None):
        self.rows = rows or {}
        self.results = list(results or [])
        self.added: list = []
        self.commits = 0
        self._next_id = 900

    async def get(self, model, pk):
        row = self.rows.get(model)
        if row is not None and getattr(row, "id", None) is not None and row.id != pk:
            return None
        return row

    async def execute(self, stmt):
        return _Rows(self.results.pop(0) if self.results else [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for o in self.added:
            if getattr(o, "id", None) is None:
                o.id = self._next_id
                self._next_id += 1

    async def commit(self):
        self.commits += 1


def make_project(**over):
    """_project_out / derive 用的全字段项目替身（用户实测口径：50 万字目标）。"""
    p = SimpleNamespace(
        id=1,
        user_id=1,
        novel_id=11,
        status="writing",
        seed_prompt="一个少年的修行故事",
        book_spec_json=json.dumps({"genre": "玄幻", "premise": "少年得剑"}, ensure_ascii=False),
        genre="玄幻",
        style_notes="",
        target_total_words=1_000_000,
        target_volume_words=None,
        target_chapter_words=TARGET,
        target_volumes=None,
        target_chapters=None,
        outline_json=json.dumps({"volumes": [{"title": "第一卷", "chapters": []}]}, ensure_ascii=False),
        reference_json=None,
        deconstruct_text="",
        deconstruct_hint="",
        deconstruct_draft_json=None,
        setup_llm="",
        outline_llm="",
        chapter_llm="",
        summary_llm="",
        review_llm="",
        global_summary="",
        auto_mode=False,
        author_intent="",
        current_focus="",
        particle_ledger="",
        subplot_board="",
        synopsis_json=None,
        market_json=None,
        cover_prompt=None,
        kb_query=None,
        platform="",
        custom_words="",
        tokens_prompt=0,
        tokens_completion=0,
        nightly_enabled=False,
        nightly_chapters=3,
        nightly_last_run=None,
        chapter_count=0,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    for k, v in over.items():
        setattr(p, k, v)
    return p


def long_text(n_chars: int, seed: str = "山风把火把吹得明灭不定，他握紧刀继续往前走。") -> str:
    """造 n_chars 字左右的正文（count_words 去空白计数）。"""
    out = []
    n = 0
    while n < n_chars:
        out.append(seed)
        n += len(seed)
    return "".join(out)[:n_chars]


def chapterish_text(n_chars: int, seed: str = "山风把火把吹得明灭不定，他握紧刀继续往前走。") -> str:
    """造带段落（每段约 200 字）的正文——拆分按双换行分段装桶，无段落永远拆不开。"""
    paras: list[str] = []
    n = 0
    while n < n_chars:
        paras.append(long_text(min(200, n_chars - n), seed=seed))
        n += 200
    return "\n\n".join(paras)


# ================================================================ C：章数推导


class TestDeriveTargetChapters:
    def test_derives_from_total_and_per(self):
        p = make_project(target_chapters=None, target_total_words=1_000_000, target_chapter_words=2000)
        assert factory_mod.derive_target_chapters(p) == 500

    def test_ceils_fraction(self):
        p = make_project(target_chapters=None, target_total_words=10_001, target_chapter_words=2000)
        assert factory_mod.derive_target_chapters(p) == 6, "10,001 ÷ 2,000 必须向上取整"

    def test_explicit_chapters_win(self):
        p = make_project(target_chapters=42, target_total_words=1_000_000, target_chapter_words=2000)
        assert factory_mod.derive_target_chapters(p) == 42, "作者显式填写的章数必须原样尊重"

    def test_missing_total_is_none(self):
        p = make_project(target_chapters=None, target_total_words=None, target_chapter_words=2000)
        assert factory_mod.derive_target_chapters(p) is None

    def test_missing_per_is_none(self):
        p = make_project(target_chapters=None, target_total_words=1_000_000, target_chapter_words=None)
        assert factory_mod.derive_target_chapters(p) is None

    def test_project_out_carries_derived(self):
        p = make_project()
        out = factory_mod._project_out(p)
        assert out["derived_target_chapters"] == 500, "项目设置响应必须带推导章数（前端设置卡要用）"

    def test_project_out_derived_none_when_unusable(self):
        p = make_project(target_total_words=None, target_chapter_words=None)
        assert factory_mod._project_out(p)["derived_target_chapters"] is None


class TestOutlineWholeBookTarget:
    """首次大纲 prompt：全书目标讲清楚，本次只出 min(推导值, 30) 章。"""

    def _run_outline(self, monkeypatch, p, *, reply=None, captured):
        p.status = "outline"  # outline_project 的守卫：只有 setup/outline 能生成大纲
        db = SeqDB(results=[[], [], [], []])  # chars / old_chapters / old_volumes / old_jobs
        monkeypatch.setattr(factory_mod, "_get_project", _always_project(p))
        monkeypatch.setattr(factory_mod, "_pick_config", lambda *a, **k: _cfg_coro())

        async def fake_chat(config, system, prompt, **kw):
            captured["prompt"] = prompt
            return reply or json.dumps(
                {"volumes": [{"title": "第一卷 起步", "chapters": [{"title": f"第{i}章", "outline": "具体事件"} for i in range(30)]}]},
                ensure_ascii=False,
            )

        monkeypatch.setattr(factory_mod, "_chat_text", fake_chat)
        return asyncio.run(factory_mod.outline_project(1, fake_user(), db))

    def test_prompt_states_whole_book_and_caps_batch_at_30(self, monkeypatch):
        """1,000,000 ÷ 2,000 = 500 章 → prompt 必须写明全书约 500 章，本次只先出 30 章。"""
        p = make_project(target_chapters=None, target_total_words=1_000_000, target_chapter_words=2000)
        captured: dict = {}
        out = self._run_outline(monkeypatch, p, captured=captured)
        prompt = captured["prompt"]
        assert "本书计划约 500 章" in prompt, f"prompt 必须讲清全书目标，实得：{prompt[:400]}"
        assert "本次先出" in prompt and "30" in prompt
        assert out["chapter_count"] == 30, "本次建骨架不得超过 30 章（一次喂 500 章不现实）"

    def test_explicit_50_chapters_also_capped_at_30(self, monkeypatch):
        p = make_project(target_chapters=50, target_total_words=None, target_chapter_words=2000)
        captured: dict = {}
        out = self._run_outline(monkeypatch, p, captured=captured)
        assert "本书计划约 50 章" in captured["prompt"]
        assert out["chapter_count"] == 30, "显式章数 50 也要按 min(50, 30) 分批出"

    def test_no_targets_defaults_30(self, monkeypatch):
        p = make_project(target_total_words=None, target_chapter_words=None, target_chapters=None)
        captured: dict = {}
        out = self._run_outline(monkeypatch, p, captured=captured)
        assert out["chapter_count"] == 30
        assert "本书计划约" not in captured["prompt"], "没有可推导目标就不许编造全书章数"


def _always_project(p):
    async def _get_project(project_id, user, session):
        return p

    return _get_project


def _cfg_coro():
    async def _c():
        return make_ai_config()

    return _c()


# ================================================================ A1：prompt 硬化


class TestWordFloorPrompt:
    def _bundle(self):
        p = SimpleNamespace(
            id=1,
            user_id=1,
            status="writing",
            novel_id=11,
            book_spec_json="",
            style_notes="",
            global_summary="",
            character_state="",
            plot_arcs="",
            kb_query=None,
            context_recent_chapters=2,
            context_extra_chapters="[]",
            target_chapter_words=TARGET,
            author_intent="",
            current_focus="",
        )
        novel = SimpleNamespace(id=11, title="测试之书")
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="开局", sort_order=0, content="", status="draft")
        job = SimpleNamespace(id=7, project_id=1, chapter_id=21, status="pending", outline="主角入局", summary="")
        return p, novel, chapter, job

    def test_context_states_hard_floor_not_float(self):
        """单章 generate / 夜跑 / 批量三条路径共用的 _assemble_context 必须写硬下限。"""
        p, novel, chapter, job = self._bundle()
        db = SeqDB(results=[[], [chapter], [], []])  # chars / chapters / volumes / done_jobs
        ctx = asyncio.run(factory_mod._assemble_context(p, novel, chapter, job, db))
        assert f"不少于 {TARGET} 字" in ctx, f"必须出现硬下限表述，实得尾部：{ctx[-300:]}"
        assert "硬下限" in ctx
        assert f"{TARGET}～{int(TARGET * 1.3)}" in ctx, "必须给出 X～1.3X 的建议区间"
        assert "±20%" not in ctx, "旧浮动口径必须消失"

    def test_no_target_stays_silent(self):
        p, novel, chapter, job = self._bundle()
        p.target_chapter_words = None
        db = SeqDB(results=[[], [chapter], [], []])
        ctx = asyncio.run(factory_mod._assemble_context(p, novel, chapter, job, db))
        assert "硬下限" not in ctx and "不少于" not in ctx


# ================================================================ A2a：非流式补足


class TestEnsureMinWords:
    """夜跑/批量路径的生成后补足：count_words < X → _chat_text 续写一轮并拼接。"""

    def _cfg(self):
        return make_ai_config()

    def test_below_floor_triggers_one_continuation_round(self, monkeypatch):
        base = "开局一段短短的正文。"  # ~10 字
        cont = long_text(1800, seed="他翻过山脊，看见远处的城墙在暮色里亮起灯火。")

        async def fake_chat(config, system, prompt, **kw):
            return cont

        monkeypatch.setattr(factory_mod, "_chat_text", fake_chat)
        out = asyncio.run(factory_mod._ensure_min_words(self._cfg(), "sys", base, TARGET))
        assert base in out and cont in out, "拼接必须保留原文并接上续写"
        assert out.index(base) < out.index(cont), "续写必须接在原文之后"

    def test_meets_floor_skips_llm(self, monkeypatch):
        text = long_text(TARGET)

        async def boom(*a, **k):
            raise AssertionError("已达标不许再发起续写调用")

        monkeypatch.setattr(factory_mod, "_chat_text", boom)
        out = asyncio.run(factory_mod._ensure_min_words(self._cfg(), "sys", text, TARGET))
        assert out == text

    def test_over_hard_cap_skips(self, monkeypatch):
        text = long_text(TARGET * 2 + 100)

        async def boom(*a, **k):
            raise AssertionError("超过 2 倍上限必须停止补足（防爆）")

        monkeypatch.setattr(factory_mod, "_chat_text", boom)
        out = asyncio.run(factory_mod._ensure_min_words(self._cfg(), "sys", text, TARGET))
        assert out == text

    def test_zero_target_is_noop(self, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("未设目标不许补足")

        monkeypatch.setattr(factory_mod, "_chat_text", boom)
        assert asyncio.run(factory_mod._ensure_min_words(self._cfg(), "sys", "正文", 0)) == "正文"

    def test_continuation_failure_keeps_text(self, monkeypatch):
        base = "一段还不算长的正文。"

        async def explode(*a, **k):
            raise RuntimeError("上游炸了")

        monkeypatch.setattr(factory_mod, "_chat_text", explode)
        out = asyncio.run(factory_mod._ensure_min_words(self._cfg(), "sys", base, TARGET))
        assert out == base, "补足失败必须保留现状（不算失败，不许把异常抛进夜跑主链）"

    def test_continuation_prompt_carries_context_and_floor(self, monkeypatch):
        base = long_text(600, seed="主角推开门，屋里的灰尘在光柱里浮动。")
        seen: dict = {}

        async def fake_chat(config, system, prompt, **kw):
            seen["prompt"] = prompt
            return long_text(1600)

        monkeypatch.setattr(factory_mod, "_chat_text", fake_chat)
        asyncio.run(factory_mod._ensure_min_words(self._cfg(), "sys", base, TARGET))
        assert f"不少于 {TARGET} 字" in seen["prompt"], "续写 prompt 必须重申硬下限"
        assert "衔接" in seen["prompt"] or "继续写" in seen["prompt"]
        assert "主角推开门" in seen["prompt"], "续写必须带已生成正文作上下文"


# ================================================================ A2b：UI 流式补足


def sse_frame(**payload) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


async def inner_stream(pieces, *, error=None, with_done=True):
    """模拟 _stream_openai 的内层输出（SSE 帧字符串序列）。"""
    yield sse_frame(stage="connected")
    for t in pieces:
        yield sse_frame(content=t)
    if error is not None:
        yield sse_frame(error=error)
    elif with_done:
        yield sse_frame(done=True, chars=sum(len(t) for t in pieces))


def fake_stream_openai(rounds, calls):
    """按调用次序返回预设轮次的 _stream_openai 替身（每轮 connected→content…→done）。"""

    async def fake(config, messages, on_complete=None, prepare=None, on_error=None):
        calls.append({"config": config, "messages": messages})
        idx = min(len(calls) - 1, len(rounds) - 1)

        async def gen():
            yield sse_frame(stage="connected")
            yield sse_frame(stage="model", model="test-model")
            for t in rounds[idx]:
                yield sse_frame(content=t)
            yield sse_frame(done=True, chars=sum(len(t) for t in rounds[idx]))

        # _stream_openai 真身返回 StreamingResponse——替身同形状（body_iterator）
        return SimpleNamespace(body_iterator=gen())

    return fake


def collected(chunks):
    return [json.loads(line[5:].strip()) for chunk in chunks for line in chunk.splitlines() if line.startswith("data:")]


class TestTopupStream:
    """_topup_stream：内层流结束后按累积字数决定续写轮，帧协议保持 {"content": …}。"""

    async def _run(self, inner, *, target=TARGET, config_box=None, fake=None):
        box = config_box if config_box is not None else [make_ai_config()]
        it = factory_mod._topup_stream(inner, job_id=7, target_words=target, config_box=box)
        return [chunk async for chunk in it]

    def test_below_floor_appends_continuation_frames(self, monkeypatch):
        first = ["桥上的风很大。", "他握紧了刀。"]
        second = [long_text(2100)]
        calls: list = []
        monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai([second], calls))

        chunks = asyncio.run(self._run(inner_stream(first)))
        events = collected(chunks)
        contents = [e["content"] for e in events if "content" in e]
        assert "".join(contents) == "".join(first) + "".join(second), "续写内容必须透传进同一条流"
        dones = [e for e in events if e.get("done")]
        assert len(dones) == 1, f"全程只能有统一收尾的一帧 done，实得 {len(dones)}"
        assert dones[0]["chars"] == len("".join(first) + "".join(second)), "chars 必须汇总两段"
        stages = [e for e in events if "stage" in e]
        assert len(stages) == 1, "续写轮的 connected/model 帧必须拦下，不许让前端看到第二次握手"

    def test_continuation_prompt_carries_text_and_floor(self, monkeypatch):
        first = [long_text(600, seed="主角推开门，屋里的灰尘在光柱里浮动。")]
        second = [long_text(2100)]
        calls: list = []
        monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai([second], calls))
        cfg = make_ai_config()
        asyncio.run(self._run(inner_stream(first), config_box=[cfg]))
        assert len(calls) == 1
        msgs = calls[0]["messages"]
        assert calls[0]["config"] is cfg, "续写必须复用本次生成的模型配置"
        user = msgs[-1]["content"]
        assert f"不少于 {TARGET} 字" in user
        assert "衔接" in user or "继续写" in user
        assert "主角推开门" in user, "续写必须带已生成正文作上下文"

    def test_meets_floor_passes_through_without_continuation(self, monkeypatch):
        text = long_text(TARGET)

        async def boom(*a, **k):
            raise AssertionError("已达标不许发起续写")

        monkeypatch.setattr(ai_mod, "_stream_openai", boom)
        chunks = asyncio.run(self._run(inner_stream([text])))
        events = collected(chunks)
        dones = [e for e in events if e.get("done")]
        assert len(dones) == 1 and dones[0]["chars"] == len(text), "达标时行为与旧版完全一致（done 拦下重发，帧数不变）"

    def test_hard_cap_blocks_topup(self, monkeypatch):
        text = long_text(TARGET * 2 + 50)

        async def boom(*a, **k):
            raise AssertionError("超过 2 倍上限必须停止补足（防爆）")

        monkeypatch.setattr(ai_mod, "_stream_openai", boom)
        chunks = asyncio.run(self._run(inner_stream([text])))
        assert sum(1 for e in collected(chunks) if e.get("done")) == 1

    def test_inner_error_blocks_topup_and_passes_error(self, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("error 帧之后不许发起续写")

        monkeypatch.setattr(ai_mod, "_stream_openai", boom)
        chunks = asyncio.run(self._run(inner_stream(["写了一半。"], error="AI 接口返回 524")))
        events = collected(chunks)
        assert any("error" in e for e in events), "内层 error 必须照常透传（旧契约）"
        assert not any(e.get("done") for e in events), "error 路径不发 done（保持旧收尾形状）"

    def test_continuation_error_is_swallowed_and_done_sent(self, monkeypatch):
        """补足轮上游失败：不算失败——正文已有，error 不透传，最后照发 done。"""
        first = ["开头只有这么一点。"]

        async def failing_fake(config, messages, on_complete=None, prepare=None, on_error=None):
            async def gen():
                yield sse_frame(stage="connected")
                yield sse_frame(error="上游 502")
                yield sse_frame(done=True, chars=0)

            return SimpleNamespace(body_iterator=gen())

        monkeypatch.setattr(ai_mod, "_stream_openai", failing_fake)
        chunks = asyncio.run(self._run(inner_stream(first)))
        events = collected(chunks)
        assert not any("error" in e for e in events), "补足轮的失败不许打扰客户端"
        dones = [e for e in events if e.get("done")]
        assert len(dones) == 1 and dones[0]["chars"] == len("".join(first))

    def test_max_two_rounds(self, monkeypatch):
        """每轮续写都只给一点点：最多补 2 轮，绝不死循环。"""
        first = ["太短了。"]
        rounds = [[long_text(300, seed="他又走了一程。")] for _ in range(5)]
        calls: list = []
        monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai(rounds, calls))
        chunks = asyncio.run(self._run(inner_stream(first)))
        assert len(calls) == 2, f"补足上限必须是 2 轮，实得 {len(calls)}"
        assert sum(1 for e in collected(chunks) if e.get("done")) == 1

    def test_zero_target_is_passthrough(self):
        """未设字数目标：帧原样转发（含内层 done），零行为变化。"""
        chunks = asyncio.run(self._run(inner_stream(["正文一段。"])))
        events = collected(chunks)
        assert [e for e in events if e.get("done")][0]["chars"] == len("正文一段。")
        assert not any("stage" in e and e["stage"] != "connected" for e in events)


    def test_content_frame_with_done_substring_passes_through(self, monkeypatch):
        """天演探针 1（复评必修 1）：正文 delta 以 \\"done 结尾——

        旧子串判帧把整帧当 done 吞掉（客户端静默丢字、chars≠实收）；
        结构化判帧后必须照常透传进 sink 与 SSE。
        """
        tricky = '她终于说："done'  # json.dumps 转义后帧尾为 \"done"} —— 子串 \"done\" 恰好命中
        first = [long_text(600, seed="山道上落满松针。"), tricky]
        second = [long_text(2100)]
        calls: list = []
        monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai([second], calls))

        chunks = asyncio.run(self._run(inner_stream(first)))
        events = collected(chunks)
        contents = "".join(e["content"] for e in events if "content" in e)
        assert tricky in contents, '含 "done 子串的正文帧必须照常透传，不许静默丢'
        dones = [e for e in events if e.get("done")]
        assert len(dones) == 1, f"全程只能有一帧统一收尾 done，实得 {len(dones)}"
        assert dones[0]["chars"] == len("".join(first) + "".join(second)), "丢帧会让 chars 与实收对不上"

    def test_content_frame_with_error_substring_does_not_abandon_topup(self, monkeypatch):
        """天演探针 2（复评必修 1）：正文 delta 与 JSON 收尾引号跨界的 \\"error——

        旧子串判帧误置 inner_failed → 该章永不补足；结构化判帧后补足轮照常发起。
        """
        tricky = '她低声念了声"error'
        first = [long_text(600, seed="山道上落满松针。"), tricky]
        second = [long_text(2100)]
        calls: list = []
        monkeypatch.setattr(ai_mod, "_stream_openai", fake_stream_openai([second], calls))

        chunks = asyncio.run(self._run(inner_stream(first)))
        events = collected(chunks)
        contents = "".join(e["content"] for e in events if "content" in e)
        assert tricky in contents, '含 "error 子串的正文帧必须照常透传'
        assert len(calls) == 1, '含 "error 子串的正文帧不得误判为失败帧——补足轮必须照常发起'


class TestTopupFullPipeline:
    """generate_chapter 全链路：包装次序（补足在内、登记/草稿在外）与既有契约。"""

    def _install(self, monkeypatch, p, db):
        from app.models import AIConfig

        async def _get_project(project_id, user, session):
            return p

        async def _pick_config(user, session, route_field):
            return AIConfig(
                id=1, user_id=1, name="测试配置", base_url="https://upstream.invalid", api_key="sk-test", model="test-model"
            )

        async def _assemble_context(project, novel, chapter, job, session):
            return "已组装的上下文：主角入局。"

        monkeypatch.setattr(factory_mod, "_get_project", _get_project)
        monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
        monkeypatch.setattr(factory_mod, "_assemble_context", _assemble_context)

        class _Cm:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(factory_mod, "SessionLocal", lambda: _Cm())

    def _upstream(self, monkeypatch, first, second):
        real = httpx.AsyncClient

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode()
            pieces = second if "【衔接续写】" in body else first

            async def stream():
                for t in pieces:
                    yield ("data: " + json.dumps({"choices": [{"delta": {"content": t}}]}) + "\n\n").encode()
                yield b"data: [DONE]\n\n"

            return httpx.Response(200, content=stream())

        def factory(*args, **kwargs):
            if kwargs.get("transport") is None:
                kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", factory)

    def test_topup_flows_into_one_stream_and_draft(self, monkeypatch):
        """不足下限：续写内容帧继续出现在同一条 SSE；草稿（FIX-7）必须含两段全文。"""
        p = SimpleNamespace(
            id=1, user_id=1, status="writing", novel_id=11, chapter_llm=None, summary_llm=None,
            target_chapter_words=TARGET, context_recent_chapters=2, reference_novel_id=None,
            author_intent="", current_focus="", tokens_prompt=0, tokens_completion=0,
        )
        job = SimpleNamespace(
            id=7, project_id=1, chapter_id=21, status="pending", attempt=0, started_at=None,
            last_error="", last_error_code="", outline="主角入局", summary="", actual_words=0,
            finished_at=None, draft_text=None, draft_updated_at=None,
        )
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="第一章", sort_order=0, content="", status="draft")
        novel = SimpleNamespace(id=11, title="测试之书", author="甲", genre="玄幻", description="")
        db = SeqDB(rows={AiChapterJob: job, Chapter: chapter, Novel: novel, AiProject: p})
        self._install(monkeypatch, p, db)
        first = ["桥上的风很大，他握紧了刀。"]
        second = [long_text(2100)]
        self._upstream(monkeypatch, first, second)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return [chunk async for chunk in resp.body_iterator]

        chunks = asyncio.run(run())
        events = collected(chunks)
        contents = [e["content"] for e in events if "content" in e]
        assert "".join(contents) == "".join(first) + "".join(second), "正文+补足必须出现在同一条流里"
        dones = [e for e in events if e.get("done")]
        assert len(dones) == 1
        assert job.draft_text == "".join(contents), "FIX-7 草稿必须覆盖补足后的全文（包装层在外收全部帧）"
        assert job.id not in factory_mod.ACTIVE_JOBS, "补足期间登记不许泄漏"

    def test_meets_floor_pipeline_is_byte_compatible(self, monkeypatch):
        """达标场景：帧序列与旧版一致——不动达标章节的行为。"""
        p = SimpleNamespace(
            id=1, user_id=1, status="writing", novel_id=11, chapter_llm=None, summary_llm=None,
            target_chapter_words=TARGET, context_recent_chapters=2, reference_novel_id=None,
            author_intent="", current_focus="", tokens_prompt=0, tokens_completion=0,
        )
        job = SimpleNamespace(
            id=7, project_id=1, chapter_id=21, status="pending", attempt=0, started_at=None,
            last_error="", last_error_code="", outline="主角入局", summary="", actual_words=0,
            finished_at=None, draft_text=None, draft_updated_at=None,
        )
        chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="第一章", sort_order=0, content="", status="draft")
        novel = SimpleNamespace(id=11, title="测试之书", author="甲", genre="玄幻", description="")
        db = SeqDB(rows={AiChapterJob: job, Chapter: chapter, Novel: novel, AiProject: p})
        self._install(monkeypatch, p, db)
        text = long_text(TARGET)
        self._upstream(monkeypatch, [text], None)

        async def run():
            resp = await factory_mod.generate_chapter(1, 7, None, fake_user(), db)
            return [chunk async for chunk in resp.body_iterator]

        chunks = asyncio.run(run())
        events = collected(chunks)
        assert "".join(e["content"] for e in events if "content" in e) == text
        assert len([e for e in events if e.get("done")]) == 1
        assert job.draft_text == text


# ================================================================ B：拆分统一


class TestSplitModule:
    def test_importable_from_chapter_split(self):
        from app.chapter_split import _maybe_split_chapter, split_long_chapter  # noqa: F401

    def test_nightly_reexport_intact(self):
        """batch.py 走 `from ..nightly import _maybe_split_chapter`——旧导入面不许断。"""
        from app.nightly import _maybe_split_chapter, split_long_chapter  # noqa: F401

    def test_split_long_chapter_buckets(self):
        from app.chapter_split import split_long_chapter

        # 两段各 2100 字的单段落：既有防失控逻辑要求每桶 ≥ target*0.4（拆太碎宁可
        # 不拆）——测试数据必须对齐这个既有契约，别把防失控误判成回归。
        text = long_text(2100, seed="他提着灯往山里走。") + "\n\n" + long_text(2100, seed="她沿着溪水追了上来。")
        parts = split_long_chapter(text, TARGET)
        assert len(parts) >= 2, "4200 字 / 2000 字目标必须拆"
        assert all(len(p) >= TARGET * 0.4 for p in parts), "不许拆出太碎的桶"

    def test_short_text_untouched(self):
        from app.chapter_split import split_long_chapter

        text = chapterish_text(1200)
        assert split_long_chapter(text, TARGET) == [text]


def make_finalize_bundle(*, text: str, target: int = TARGET):
    p = make_project(target_chapter_words=target, status="writing")
    job = SimpleNamespace(
        id=7, project_id=1, chapter_id=21, status="writing", attempt=1, started_at=None,
        last_error="", last_error_code="", outline="主角入局", summary="", actual_words=0,
        finished_at=None, draft_text=None, draft_updated_at=None,
    )
    chapter = SimpleNamespace(id=21, novel_id=11, volume_id=None, title="第一章", sort_order=10, content="", status="writing")
    novel = SimpleNamespace(id=11, title="测试之书", author="甲", genre="玄幻", description="")
    db = SeqDB(rows={AiProject: p, AiChapterJob: job, Chapter: chapter, Novel: novel})
    return p, job, chapter, novel, db


def install_finalize(monkeypatch, p, db):
    async def _get_project(project_id, user, session):
        return p

    from app.models import AIConfig

    async def _pick_config(user, session, route_field):
        return AIConfig(id=1, user_id=1, name="t", base_url="https://up.invalid", api_key="k", model="m")

    async def _update_state_files(*a, **k):
        return True

    async def _sync_relations(*a, **k):
        return None

    async def _chat_text(config, system, prompt, **kw):
        return "本章摘要：主角入局。"

    async def _snapshot(db, chapter, label):
        return None

    async def _sync_chapter(db, chapter_id):
        return None

    monkeypatch.setattr(factory_mod, "_get_project", _get_project)
    monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(factory_mod, "_update_state_files", _update_state_files)
    monkeypatch.setattr(factory_mod, "_sync_relations_from_chapter", _sync_relations)
    monkeypatch.setattr(factory_mod, "_chat_text", _chat_text)

    import app.search_fts as fts_mod
    import app.snapshot_service as snap_mod

    monkeypatch.setattr(snap_mod, "try_snapshot_before_ai", _snapshot)
    monkeypatch.setattr(fts_mod, "sync_chapter", _sync_chapter)


class TestFinalizeSplit:
    def _finalize(self, monkeypatch, p, db, text: str):
        install_finalize(monkeypatch, p, db)
        data = factory_mod.FinalizeIn(content_text=text)
        return asyncio.run(factory_mod.finalize_chapter(1, 7, data, fake_user(), db))

    def test_finalize_splits_overlong_chapter(self, monkeypatch):
        """核心诉求②：UI「逐章生成→finalize」也必须拆——此前只有夜跑/批量拆。"""
        text = chapterish_text(5200)
        p, job, chapter, novel, db = make_finalize_bundle(text=text)
        r = self._finalize(monkeypatch, p, db, text)
        assert r["ok"] is True
        assert r.get("split_into", 1) >= 2, f"5200 字必须拆分，实得 {r}"
        first = chapter.content
        from app.utils import strip_html

        first_len = len("".join(strip_html(first).split()))
        assert first_len <= TARGET * 1.6, f"原章剩余正文必须回到 [X, 1.6X] 区间，实得 {first_len}"
        new_chapters = [o for o in db.added if isinstance(o, Chapter)]
        new_jobs = [o for o in db.added if isinstance(o, AiChapterJob)]
        assert len(new_chapters) == r["split_into"] - 1
        assert len(new_jobs) == r["split_into"] - 1
        assert all(j.status == "done" for j in new_jobs), "拆出的新章沿用现有实现：直接建 done 任务"

    def test_finalize_within_threshold_no_split(self, monkeypatch):
        text = chapterish_text(3000)  # < 2000*1.6 = 3200
        p, job, chapter, novel, db = make_finalize_bundle(text=text)
        r = self._finalize(monkeypatch, p, db, text)
        assert r["ok"] is True and r.get("split_into", 1) == 1
        assert not [o for o in db.added if isinstance(o, Chapter)]

    def test_finalize_no_target_no_split(self, monkeypatch):
        text = chapterish_text(5200)
        p, job, chapter, novel, db = make_finalize_bundle(text=text)
        p.target_chapter_words = None
        r = self._finalize(monkeypatch, p, db, text)
        assert r["ok"] is True and r.get("split_into", 1) == 1
