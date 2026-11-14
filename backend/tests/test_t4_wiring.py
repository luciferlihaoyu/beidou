"""t4 五处接线的行为测试（TASK-t4-schema.md §3.2 + 碧霄硬约束 H1/H2/H3）。

钉住的契约：
- H2：五条旧 502 文案必须**逐字保留做前缀**，新原因追加在后（任务书 §1/§3.2：
  旧文案是用户看得到的契约，新文案必须是旧文案的超集）；
- H3：D2 收紧带来的三个行为变化（0 章大纲/无名角色/数字章名，旧静默落库 →
  新 502 拒收）必须有测试钉住且 docstring 写明这是**有意的收紧**；
- 裁决三：快照走 merge_snapshot（公式 B+C）——init 的 characters/自造键、
  outline 卷章自造键在快照里保真，AI 未给的字段不得出现 null 键；
- H1：_save_project_wire 四节点仍然被调（t3 观测落库不许被接线拆掉；
  完整守卫由 tests/test_skill_wire.py 承接，这里只是接线层的轻哨兵）。

替身手法与 test_fix8_outline_continue 同口径（直接调端点函数 + monkeypatch
_chat_text + SeqDB），替身函数直接复用该文件，避免两份漂移。

运行：cd backend && PYTHONPATH=/data/dsh/北斗/.pydeps python -m pytest tests/test_t4_wiring.py -q
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import AiProject, Novel
from app.routers import ai_factory as factory_mod
from test_fix8_outline_continue import (  # 同目录替身：SeqDB/fake_user/make_project 等
    SeqDB,
    continue_reply,
    fake_user,
    install_core_guards,
    make_ai_config,
    make_project,
    make_skeleton,
)

_MISSING_FIELDS = {
    "auto_mode": False,
    "auto_kb_sync": False,
    "author_intent": "",
    "current_focus": "",
    "cover_prompt": None,
    "last_kb_sync_at": None,
    "skill_wire_json": "{}",
}

def _full_project(**over):
    """make_project + _project_out 全字段垫片（fix8 替身只为 continue 而写，字段覆盖不全）。"""
    p = make_project(**over)
    for k, v in _MISSING_FIELDS.items():
        if not hasattr(p, k):
            setattr(p, k, v)
    return p


_INIT_OK = {
    "titles": ["书名A", "书名B"],
    "genre": "玄幻",
    "time": "古代",
    "place": "九州",
    "theme": "复仇",
    "tone": "热血",
    "pov": "第三人称",
    "premise": "少年踏上复仇之路，发现真凶竟是至亲。",
}

_OUTLINE_REPLY = json.dumps(
    {
        "volumes": [
            {
                "title": "第一卷 出山",
                "summary": "主角离村，初入宗门",
                "chapters": [
                    {"title": "第一章", "outline": "主角在祭典上觉醒灵根"},
                    {"title": "第二章", "outline": "主角被迫离村，踏上西行路"},
                ],
            }
        ]
    },
    ensure_ascii=False,
)

_SETUP_REPLY = json.dumps(
    {
        "characters": [
            {"name": "林越", "role": "主角", "description": "沉默寡言的少年", "arc": "从懦弱到担当"},
            {"name": "苏挽", "role": "对手", "description": "名门嫡女，锋芒毕露"},
        ],
        "worldview": [{"category": "力量体系", "title": "灵脉", "content": "天地灵气自九脉而出"}],
    },
    ensure_ascii=False,
)


def _patch_llm(monkeypatch, reply):
    async def _pick_config(user, session, route_field):
        return make_ai_config()

    async def fake_chat(config, system, prompt, **kw):
        if isinstance(reply, list):
            return reply.pop(0)
        return reply

    monkeypatch.setattr(factory_mod, "_pick_config", _pick_config)
    monkeypatch.setattr(factory_mod, "_chat_text", fake_chat)


def _run(coro):
    return asyncio.run(coro)


# ================================================================ H2：旧句前缀


class TestOldMessagePrefix:
    """五条旧 502 文案逐字保留做前缀，新原因追加在后（H2 / 任务书 §3.2）。"""

    def test_init_keeps_old_prefix_and_appends_reason(self, monkeypatch):
        p = _full_project(status="draft", novel_id=None)
        _patch_llm(monkeypatch, '{"genre": "玄幻"}')  # 缺 premise
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.init_project(1, None, fake_user(), SeqDB(rows={AiProject: p})))
        assert ei.value.status_code == 502
        msg = ei.value.detail
        assert msg.startswith("AI 输出缺少核心字段（premise）"), f"旧句前缀丢失：{msg}"
        assert "premise：缺少核心梗概" in msg, f"新原因缺失：{msg}"

    def test_setup_keeps_old_prefix_and_appends_reason(self, monkeypatch):
        p = _full_project(status="setup")
        _patch_llm(monkeypatch, '{"characters": []}')
        db = SeqDB(rows={AiProject: p}, results=[[], []])
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.setup_project(1, fake_user(), db))
        assert ei.value.status_code == 502
        msg = ei.value.detail
        assert msg.startswith("AI 未生成角色"), f"旧句前缀丢失：{msg}"
        assert "不能为空数组" in msg, f"新原因缺失：{msg}"

    def test_outline_keeps_old_prefix_and_appends_reason(self, monkeypatch):
        p = _full_project(status="setup")
        _patch_llm(monkeypatch, '{"volumes": []}')
        db = SeqDB(rows={AiProject: p}, results=[[], [], []])
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.outline_project(1, fake_user(), db))
        assert ei.value.status_code == 502
        msg = ei.value.detail
        assert msg.startswith("AI 未生成大纲"), f"旧句前缀丢失：{msg}"
        assert "不能为空数组" in msg, f"新原因缺失：{msg}"

    def test_continue_keeps_old_prefix_and_appends_reason(self, monkeypatch):
        p = _full_project(status="writing")
        volumes, chapters, jobs = make_skeleton(30, 10)
        _patch_llm(monkeypatch, '{"volumes": []}')
        db = SeqDB(rows={AiProject: p}, results=[jobs, chapters, volumes])
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.outline_continue(1, fake_user(), db))
        assert ei.value.status_code == 502
        msg = ei.value.detail
        assert msg.startswith("AI 未产出任何续写章节"), f"旧句前缀丢失：{msg}"

    def test_source_keeps_all_five_old_messages(self):
        """第二道闸「AI 返回的大纲没有可用章节」在 schema 全局≥1章后不可达
        （纵深防御保留），无法经端点触发——用源码字面断言钉住五条旧句全在。"""
        import inspect

        src = inspect.getsource(factory_mod)
        for old in (
            "AI 输出缺少核心字段（premise）",
            "AI 未生成角色",
            "AI 未生成大纲",
            "AI 未产出任何续写章节",
            "AI 返回的大纲没有可用章节",
        ):
            assert old in src, f"旧 502 文案「{old}」被删——旧文案是用户契约，只能做前缀不许删"


# ================================================================ H3：行为变化


class TestBehaviorChanges:
    """D2 收紧的三个行为变化：旧静默落库 → 新 502 拒收。

    这是**有意的收紧**（碧霄 2026-12 拍板，交付回报单列《行为变化清单》由 L
    复核）：旧行为等于给用户一本空书/无名角色/"1"这种脏标题。若产品决定
    回退宽松，应改 schema 契约并同步改这里的断言，而不是悄悄放宽校验。
    """

    def test_zero_chapter_outline_now_502(self, monkeypatch):
        """以前：volumes 非空即落库，0 章大纲静默入库且状态推到 writing；
        现在：拒绝。旧行为等于给用户一本空书。"""
        p = _full_project(status="setup")
        _patch_llm(monkeypatch, '{"volumes": [{"title": "空卷", "chapters": []}]}')
        db = SeqDB(rows={AiProject: p}, results=[[], [], []])
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.outline_project(1, fake_user(), db))
        assert ei.value.status_code == 502
        assert "至少要包含 1 个章节" in ei.value.detail

    def test_nameless_character_now_502(self, monkeypatch):
        """以前：str(c.get("name","")) 落库无名角色；现在：拒绝。"""
        p = _full_project(status="setup")
        _patch_llm(monkeypatch, '{"characters": [{"role": "主角"}]}')
        db = SeqDB(rows={AiProject: p}, results=[[], []])
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.setup_project(1, fake_user(), db))
        assert ei.value.status_code == 502
        assert "角色名" in ei.value.detail

    def test_scalar_chapter_title_now_502(self, monkeypatch):
        """以前：str(1) 落库数字章名 "1"；现在：类型契约拒绝。"""
        p = _full_project(status="setup")
        _patch_llm(
            monkeypatch,
            '{"volumes": [{"title": "卷", "chapters": [{"title": 1, "outline": "要点"}]}]}',
        )
        db = SeqDB(rows={AiProject: p}, results=[[], [], []])
        with pytest.raises(HTTPException) as ei:
            _run(factory_mod.outline_project(1, fake_user(), db))
        assert ei.value.status_code == 502


# ================================================================ 裁决三：快照保真


class TestSnapshotFidelity:
    """快照走 merge_snapshot（公式 B+C）：原始保真 + 规整覆盖 + 滤新增 None。"""

    def test_init_snapshot_keeps_characters_and_extra(self, monkeypatch):
        """init 的 characters（前端角色草案卡数据源）与自造字段必须进快照；
        AI 未给的 time 不得以 null 键出现（公式 C）。"""
        p = _full_project(status="draft", novel_id=None)
        payload = {
            "titles": ["书名A"],
            "genre": "玄幻",
            "theme": "复仇",
            "premise": "少年踏上复仇之路，发现真凶竟是至亲。",
            "characters": [{"name": "林越", "role": "主角", "brief": "沉默寡言的少年刀客"}],
            "mood": "昂扬",
        }
        _patch_llm(monkeypatch, json.dumps(payload, ensure_ascii=False))
        db = SeqDB(rows={AiProject: p})
        _run(factory_mod.init_project(1, None, fake_user(), db))
        snap = json.loads(p.book_spec_json)
        assert snap["characters"][0]["brief"] == "沉默寡言的少年刀客"
        assert snap["mood"] == "昂扬"
        assert "time" not in snap, "AI 未给的字段不得以 null 键新增（公式 C）"
        assert snap["premise"] == payload["premise"]

    def test_outline_snapshot_keeps_nested_extra(self, monkeypatch):
        """卷级 notes / 章级 mood 自造键必须进 outline_json 快照（公式 B 嵌套保真）。"""
        p = _full_project(status="setup")
        payload = {
            "volumes": [
                {
                    "title": "第一卷 出山",
                    "summary": "主线",
                    "notes": "卷级自造键",
                    "chapters": [{"title": "第一章", "outline": "觉醒灵根", "mood": "压抑"}],
                }
            ]
        }
        _patch_llm(monkeypatch, json.dumps(payload, ensure_ascii=False))
        db = SeqDB(rows={AiProject: p}, results=[[], [], []])
        _run(factory_mod.outline_project(1, fake_user(), db))
        snap = json.loads(p.outline_json)
        assert snap["volumes"][0]["notes"] == "卷级自造键"
        assert snap["volumes"][0]["chapters"][0]["mood"] == "压抑"
        assert p.status == "writing"

    def test_continue_snapshot_append_keeps_extra(self, monkeypatch):
        """续写快照追加的新卷同样保自造键。"""
        p = _full_project(status="writing")
        volumes, chapters, jobs = make_skeleton(30, 10)
        reply = json.dumps(
            {
                "volumes": [
                    {
                        "title": "第四卷 风起",
                        "notes": "续写卷自造键",
                        "chapters": [{"title": "风波", "outline": "长老截杀", "mood": "紧张"}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        _patch_llm(monkeypatch, reply)
        db = SeqDB(rows={AiProject: p}, results=[jobs, chapters, volumes])
        out = _run(factory_mod.outline_continue(1, fake_user(), db))
        assert out["added_chapters"] == 1
        snap = json.loads(p.outline_json)
        new_vol = snap["volumes"][-1]
        assert new_vol["notes"] == "续写卷自造键"
        assert new_vol["chapters"][0]["mood"] == "紧张"


# ================================================================ 正常链路


class TestHappyPath:
    """正常产物接线后链路不回归。"""

    def test_init_happy_passes(self, monkeypatch):
        p = _full_project(status="draft", novel_id=None)
        _patch_llm(monkeypatch, json.dumps(_INIT_OK, ensure_ascii=False))
        db = SeqDB(rows={AiProject: p})
        out = _run(factory_mod.init_project(1, None, fake_user(), db))
        assert out["book_spec"]["premise"] == _INIT_OK["premise"]
        assert p.kb_query, "立项应预填璇玑检索词"

    def test_setup_happy_passes(self, monkeypatch):
        p = _full_project(status="setup")
        novel = SimpleNamespace(id=11, title="测试之书")
        _patch_llm(monkeypatch, _SETUP_REPLY)
        db = SeqDB(rows={AiProject: p, Novel: novel}, results=[[], []])
        out = _run(factory_mod.setup_project(1, fake_user(), db))
        assert out["book_spec"] is not None
        added_chars = [o for o in db.added if type(o).__name__ == "Character"]
        assert len(added_chars) == 2, "两个角色应落库"

    def test_outline_happy_passes(self, monkeypatch):
        p = _full_project(status="setup")
        novel = SimpleNamespace(id=11, title="测试之书")
        _patch_llm(monkeypatch, _OUTLINE_REPLY)
        db = SeqDB(rows={AiProject: p, Novel: novel}, results=[[], [], []])
        out = _run(factory_mod.outline_project(1, fake_user(), db))
        assert out.get("chapter_count") == 2 or out.get("total_chapters") == 2
        assert p.status == "writing"

    def test_continue_happy_passes(self, monkeypatch):
        p = _full_project(status="writing")
        volumes, chapters, jobs = make_skeleton(30, 10)
        _patch_llm(monkeypatch, continue_reply(n_volumes=1, per=5))
        db = SeqDB(rows={AiProject: p}, results=[jobs, chapters, volumes])
        out = _run(factory_mod.outline_continue(1, fake_user(), db))
        assert out["ok"] is True and out["added_chapters"] == 5


# ================================================================ H1：观测轻哨兵


class TestWireObservationSentinel:
    """H1：接线不许拆 t3 观测链——四节点 _save_project_wire 必须仍被调。
    完整守卫（六个项目级键齐全落库）由 tests/test_skill_wire.py 承接。"""

    def test_save_project_wire_called_on_all_four_nodes(self, monkeypatch):
        calls: list[str] = []
        orig = factory_mod._save_project_wire

        def spy(p, node, record):
            assert node in ("init", "setup", "outline", "outline_continue")
            assert record, f"t3 观测记录被掏空（node={node} record={record!r}）——H1 红线"
            calls.append(node)
            return orig(p, node, record)

        monkeypatch.setattr(factory_mod, "_save_project_wire", spy)

        _patch_llm(monkeypatch, json.dumps(_INIT_OK, ensure_ascii=False))
        p = _full_project(status="draft", novel_id=None)
        _run(factory_mod.init_project(1, None, fake_user(), SeqDB(rows={AiProject: p})))

        _patch_llm(monkeypatch, _SETUP_REPLY)
        p2 = _full_project(status="setup")
        _run(factory_mod.setup_project(1, fake_user(), SeqDB(rows={AiProject: p2}, results=[[], []])))

        _patch_llm(monkeypatch, _OUTLINE_REPLY)
        p3 = _full_project(status="setup")
        _run(
            factory_mod.outline_project(
                1, fake_user(), SeqDB(rows={AiProject: p3}, results=[[], [], []])
            )
        )

        _patch_llm(monkeypatch, continue_reply(n_volumes=1, per=5))
        p4 = _full_project(status="writing")
        vols, chaps, jobs = make_skeleton(30, 10)
        _run(factory_mod.outline_continue(1, fake_user(), SeqDB(rows={AiProject: p4}, results=[jobs, chaps, vols])))

        assert calls == ["init", "setup", "outline", "outline_continue"], f"t3 观测落库被接线拆掉了：{calls}"


# ================================================================ 50K 软约束


class TestSnapshotWarnSoftCap:
    """50K 软约束经端点生效：只告警不阻断，阈值 env 可覆盖。"""

    def test_oversized_snapshot_warns_and_still_saves(self, monkeypatch, caplog):
        import logging

        monkeypatch.setenv("T4_SNAPSHOT_WARN_CHARS", "50")
        p = _full_project(status="draft", novel_id=None)
        payload = {**_INIT_OK, "notes": "超" * 200}
        _patch_llm(monkeypatch, json.dumps(payload, ensure_ascii=False))
        _run(factory_mod.init_project(1, None, fake_user(), SeqDB(rows={AiProject: p})))
        assert any(r.levelno == logging.WARNING for r in caplog.records), "超长快照必须留 warning"
        snap = json.loads(p.book_spec_json)
        assert snap["notes"] == "超" * 200, "软约束只告警，落库内容不许被截断"


# ================================================================ t4b：截断口径诚实性


def _reply_vols(chapter_vols, n_vols=12, per=1):
    """造 outline_continue 产物：n_vols 卷，只有 chapter_vols 里的卷带章（各 per 章）。"""
    volumes = [
        {
            "title": f"第{i}卷",
            "chapters": [{"title": f"第{i}卷第{c}章", "outline": f"事件{i}-{c}"} for c in range(1, per + 1)],
        }
        if i in chapter_vols
        else {"title": f"第{i}卷", "chapters": []}
        for i in range(1, n_vols + 1)
    ]
    return json.dumps({"volumes": volumes}, ensure_ascii=False)


class TestTruncationHonesty:
    """落库循环是**截断口径**（volumes[:10]、chapters[:40]），schema 是**全局口径**。
    两者不等价，由此钉两条：① `added == 0` 闸不是死代码；② 用户可见数字必须是真实入库数。"""

    def test_chapter_beyond_tenth_volume_still_hits_the_502_gate(self, monkeypatch):
        """12 卷、唯一的章在第 12 卷：schema 放行（全局 ≥1 章成立），
        但循环只走前 10 卷 → added == 0 → **必须**报 502。

        这条测试**天生是绿的**——它的价值在变异上：
        若有人以「schema 已经保证过了」为由删掉 `if added == 0`，这条立刻变红。
        """
        p = _full_project(status="writing")
        vols, chaps, jobs = make_skeleton(30, 10)
        _patch_llm(monkeypatch, _reply_vols(chapter_vols={12}))
        db = SeqDB(rows={AiProject: p}, results=[jobs, chaps, vols])
        with pytest.raises(HTTPException) as exc:
            _run(factory_mod.outline_continue(1, fake_user(), db))
        assert exc.value.status_code == 502
        assert "AI 返回的大纲没有可用章节" in str(exc.value.detail), (
            f"截断口径下的空入库必须报旧句文案，实际：{exc.value.detail}"
        )

    def test_message_reports_stored_volumes_not_the_llm_count(self, monkeypatch, caplog):
        """12 卷各 1 章：循环只入前 10 卷 → 消息与 added_volumes 必须报 **10**（不是 12），
        并且丢弃必须留 warning（静默丢用户数据不可接受）。"""
        import logging

        p = _full_project(status="writing")
        vols, chaps, jobs = make_skeleton(30, 10)
        _patch_llm(monkeypatch, _reply_vols(chapter_vols=set(range(1, 13))))
        db = SeqDB(rows={AiProject: p}, results=[jobs, chaps, vols])
        caplog.set_level(logging.WARNING, logger=factory_mod.logger.name)
        # 必须在调用**之前**读——端点会原地改写 p.outline_json
        before = len(json.loads(p.outline_json or "{}").get("volumes", []))
        out = _run(factory_mod.outline_continue(1, fake_user(), db))
        assert out["added_chapters"] == 10, f"只应入库前 10 卷的章：{out['added_chapters']}"
        assert out["added_volumes"] == 10, (
            f"added_volumes 报的是 LLM 给的卷数还是实际入库卷数？期望 10，实得 {out['added_volumes']}"
        )
        assert "10 卷" in out["message"] and "12 卷" not in out["message"], (
            f"消息里的卷数必须是真实入库数，实得：{out['message']}"
        )
        warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("12" in w and "10" in w for w in warns), (
            f"截断丢弃必须留 warning 并写清 12→10，实得：{warns}"
        )
        # 快照同样不许写假数据：库里只建了 10 卷，outline_json 就不许出现第 11/12 卷
        # （断言用「前后差」而非绝对数——夹具里已有几卷不该影响判据）
        snap_titles = [v.get("title") for v in json.loads(p.outline_json or "{}").get("volumes", [])]
        assert len(snap_titles) == before + 10, (
            f"快照只应追加实际入库的 10 卷（原有 {before} 卷），实得新增 {len(snap_titles) - before} 卷：{snap_titles}"
        )
        assert "第11卷" not in snap_titles and "第12卷" not in snap_titles, (
            f"快照里出现了库里并不存在的卷（前端大纲卡会显示假数据）：{snap_titles}"
        )

    def test_oversized_volume_drops_chapters_visibly(self, monkeypatch, caplog):
        """单卷 45 章：只入前 40 章，丢弃 5 章必须留 warning（同一判据的另一档）。"""
        import logging

        p = _full_project(status="writing")
        vols, chaps, jobs = make_skeleton(30, 10)
        reply = json.dumps(
            {"volumes": [{"title": "大卷", "chapters": [{"title": f"章{c}", "outline": "o"} for c in range(1, 46)]}]},
            ensure_ascii=False,
        )
        _patch_llm(monkeypatch, reply)
        db = SeqDB(rows={AiProject: p}, results=[jobs, chaps, vols])
        caplog.set_level(logging.WARNING, logger=factory_mod.logger.name)
        out = _run(factory_mod.outline_continue(1, fake_user(), db))

        assert out["added_chapters"] == 40
        warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("45" in w and "40" in w for w in warns), f"每卷超 40 章的丢弃必须可见，实得：{warns}"
        # 碧霄自查补完：上一轮 t4b 只裁了「卷」没裁「卷内的章」——库 40 条、快照 45 条，
        # 同一个假数据洞换了个维度还开着。这条断言把它钉死。
        snap_last = json.loads(p.outline_json or "{}")["volumes"][-1]
        assert len(snap_last.get("chapters") or []) == 40, (
            f"快照写了 {len(snap_last.get('chapters') or [])} 章而库里只有 40 章——"
            "前端大纲卡会渲染出库中不存在的章（持久化假数据）"
        )

    # ---------- t4b 同款判据贯彻到主路径 outline_project（天演 C-1）----------
    # 库循环 volumes[:20]/chapters[:100] 是截断口径；outline_json 快照照原样写
    # 全量就是**持久化的假数据**——前端大纲卡会渲染出库里根本不存在的卷/章。

    def _run_main_outline(self, monkeypatch, payload_volumes, caplog):
        """主路径共用跑法：25 卷/105 章这类超量输入打 outline_project 端点。"""
        import logging

        reply = json.dumps({"volumes": payload_volumes}, ensure_ascii=False)
        p = _full_project(status="setup")
        novel = SimpleNamespace(id=11, title="测试之书")
        _patch_llm(monkeypatch, reply)
        db = SeqDB(rows={AiProject: p, Novel: novel}, results=[[], [], []])
        caplog.set_level(logging.WARNING, logger=factory_mod.logger.name)
        _run(factory_mod.outline_project(1, fake_user(), db))
        return p, db

    def test_main_path_over_20_volumes_snapshot_stays_honest(self, monkeypatch, caplog):
        """25 卷输入：库建 20 卷，快照也必须只有 20 卷，且丢弃留 warning。

        变异判据：摘掉主路径快照裁剪（或库循环 [:20]）→ 本条必红。"""
        import logging

        volumes = [
            {
                "title": f"第{i}卷",
                "chapters": [{"title": f"第{i}卷第1章", "outline": f"事件{i}"}],
            }
            for i in range(1, 26)
        ]
        p, db = self._run_main_outline(monkeypatch, volumes, caplog)

        db_vols = [o for o in db.added if type(o).__name__ == "Volume"]
        db_chaps = [o for o in db.added if type(o).__name__ == "Chapter"]
        assert len(db_vols) == 20, f"库循环只应建 20 卷，实得 {len(db_vols)}"
        assert len(db_chaps) == 20, f"20 卷各 1 章应建 20 章，实得 {len(db_chaps)}"

        snap = json.loads(p.outline_json)
        snap_titles = [v.get("title") for v in snap["volumes"]]
        assert len(snap_titles) == 20, (
            f"快照必须按入库口径裁剪到 20 卷（写 25 卷就是持久化假数据），"
            f"实得 {len(snap_titles)} 卷：{snap_titles}"
        )
        assert "第21卷" not in snap_titles and "第25卷" not in snap_titles, (
            f"快照里出现了库里并不存在的卷：{snap_titles}"
        )
        warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("25" in w and "20" in w for w in warns), (
            f"丢弃 5 卷必须留 warning 写清 25→20，实得：{warns}"
        )

    def test_main_path_over_100_chapters_snapshot_stays_honest(self, monkeypatch, caplog):
        """单卷 105 章：库入 100 章，快照同卷也必须只有 100 章，且丢弃留 warning。

        变异判据：摘掉主路径快照裁剪（或库循环 [:100]）→ 本条必红。"""
        import logging

        big_vol = {
            "title": "大卷",
            "chapters": [{"title": f"第{c}章", "outline": f"要点{c}"} for c in range(1, 106)],
        }
        p, db = self._run_main_outline(monkeypatch, [big_vol], caplog)

        db_chaps = [o for o in db.added if type(o).__name__ == "Chapter"]
        assert len(db_chaps) == 100, f"单卷只应入库 100 章，实得 {len(db_chaps)}"

        snap = json.loads(p.outline_json)
        assert len(snap["volumes"]) == 1, f"快照应有 1 卷，实得 {len(snap['volumes'])}"
        snap_chaps = snap["volumes"][0].get("chapters", [])
        assert len(snap_chaps) == 100, (
            f"快照必须按入库口径裁剪到 100 章（写 105 章就是持久化假数据），"
            f"实得 {len(snap_chaps)} 章"
        )
        warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("105" in w and "100" in w for w in warns), (
            f"丢弃 5 章必须留 warning 写清 105→100，实得：{warns}"
        )
