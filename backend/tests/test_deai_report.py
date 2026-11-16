"""t8-deai-report：自包含 HTML 检测报告的守卫（碧霄亲自实现，因派发通道连续 4 次零产出中断）。

判据分四组，每组都配了变异靶点（见台账与 commit message）。

**形状事实（实测 t7@57b29fa，不是设计愿望）**：
`collect_signals(text, ...)` 顶层键 = rule / perplexity / reviewer / style / fusion，
**没有 coverage 键、也不含原文**。所以：
- 覆盖率证据 = 从 fusion.participating + 各路 status **推导**（不假装 t7 给了现成字段）；
- 原文与规则命中由 **meta** 传入；没传就在报告里**如实写"未随结果传入"**，
  绝不留空让人误读成"没命中"。
"""

import json
import re
from types import SimpleNamespace

import pytest

from app.deai_report import build_report_html
from app import deai_signals as ds

# 期望串**硬编码**在本文件里，不从实现 import（本项目铁律：期望值不许从实现读）
CAVEAT = "实验风险分不是 AI 生成概率"


def _result(**over):
    """一份三路都不理想的真实形状结果（t7@57b29fa 实测键名手抄，含 skipped/failed/uncalibrated）。"""
    r = {
        "rule": {
            "signal": "rule",
            "status": "ok",
            "value": 61.5,
            "detail": "本地确定性规则检测（9 维，无模型参与）",
        },
        "perplexity": {
            "signal": "perplexity",
            "status": "skipped",
            "value": None,
            "detail": "当前模型未返回 logprobs，此路未参与打分",
        },
        "reviewer": {
            "signal": "reviewer",
            "status": "failed",
            "value": None,
            "detail": "审校师调用/解析失败：RuntimeError: 上游 503",
        },
        "style": {
            name: {
                "value": None,
                "human_low": None,
                "human_high": None,
                "deviation": None,
                "status": "uncalibrated",
                "detail": "缺少标定样本，区间未建立",
            }
            for name in ds.DIMENSION_NAMES
        },
        "fusion": {
            "score": 38.5,
            "participating": ["rule"],
            "weights": {"rule": 0.5},
            "detail": "仅 1 路参与（rule）；其余：perplexity=skipped、reviewer=failed —— 分数只反映这一路",
            "caveat": CAVEAT,
        },
    }
    r.update(over)
    return r


META = {
    "project_id": 7,
    "book_title": "断玉记",
    "chapter_no": 12,
    "text": "他深吸一口气。\n\n「你走吧。」她说，转身下山。",
    "issues": [
        {"type": "疲劳词", "severity": "high", "detail": "套词「深吸一口气」×2"},
        {"type": "对话标签", "severity": "low", "detail": "「她说」类标签占比 60%"},
    ],
}


def _html(**over):
    return build_report_html(_result(**over), dict(META))


# ================================================================ A 组：安全


class TestSafety:
    def test_no_script_tag_at_all(self):
        """报告自身不许输出任何 <script> —— 自包含 HTML 不需要脚本。"""
        out = _html()
        assert "<script" not in out.lower(), "报告里出现了 script 标签（自包含 HTML 不该有可执行脚本）"

    def test_no_external_resource_reference(self):
        """不许外链 css/js/图片：双击可开 = 断网也能看。"""
        out = _html()
        hits = re.findall(r'(?:src|href)\s*=\s*["\']?(?:https?:)?//', out, re.I)
        assert not hits, f"存在外链资源引用 {hits[:3]}（离线打开会缺样式，Ctrl+P 存档也会坏）"

    def test_no_inline_event_handlers(self):
        out = _html()
        assert not re.search(r"\son[a-z]+\s*=", out, re.I), "报告里出现 on* 事件属性（可执行入口）"

    def test_malicious_text_is_escaped_into_visible_text(self):
        """🔴 检测原文本身就是**不可信输入**（模型写的、人改的）。
        原文里的 <script>alert(1)</script> 与 <img src=...> 必须变成**可见文本**，
        而不是页面里的真标签——否则一份被投毒的书稿就能在报告里执行脚本。
        """
        poison = '<script>alert(1)</script> <img src=x onerror=alert(2)> <a href="javascript:alert(3)">'
        out = build_report_html(_result(), {**META, "text": poison})
        low = out.lower()
        assert "<script>alert" not in low, "原文里的 script 标签没被转义，直接进了 DOM"
        # 🔴 判据形状修正（我第一版写错了）：转义后 `onerror=alert(2)` 作为**可见文本**
        # 那串字符本来就该还在——要钉的不是"字符串消失"，而是**不许存在未转义的标签/属性形态**。
        # 否则判据会把"安全地显示攻击样本"误判成不安全。
        assert "<img src=x" not in low, "原文里的 img 标签没被转义"
        assert '<a href="javascript:' not in low, "原文里的 javascript: 链接没被转义"
        assert "&lt;img" in out and "&lt;script&gt;" in out, "应看到转义后的可见文本"
        # 转义后作为**文本**必须仍在（不许为了安全把原文整段丢掉）
        assert "alert(1)" in out, "安全处理把原文吞了——报告必须还能看到被检文本"


# ================================================================ B 组：诚实


class TestHonesty:
    def test_three_unavailable_states_are_visible(self):
        """skipped / failed / uncalibrated 三种状态必须**看得见**。
        只渲染成功项 = 把"没检测"伪装成"没问题"，这是本项目反复出现的老错。
        """
        out = _html()
        assert "未启用" in out, "perplexity 的 skipped 状态没出现在报告里"
        assert "失败" in out, "reviewer 的 failed 状态没出现在报告里"
        assert "未标定" in out, "9 维文体指标的 uncalibrated 状态没出现在报告里"
        # 而且要出现在**对应那一行**，不是全文随便有个词
        assert out.index("未启用") < out.index("未标定"), "三路信号表应排在文体对照表之前"

    def test_failed_reviewer_is_not_rendered_as_no_findings(self):
        """审校师失败时，那一格不许是空的或"未发现问题"。

        🔴 碧霄自查补强：初版这条只查了 `"未发现问题" not in out` 与"原因在不在"，
        变异「把 `if status != ok` 改成 `if False`」（即失败照走成功分支、渲染成空清单）
        **14 passed 全绿**——判据对这个形状是空的，与批次一 MJ-4 同一族错误。
        现在钉的是**审校师证据那一段本身**：失败标记必须在，空清单占位不许出现。
        """
        out = _html()
        assert "未发现问题" not in out, "调用失败被渲染成了「未发现问题」——失败不是没问题"
        seg = out[out.index("审校师证据") :]
        assert "失败" in seg or "503" in seg, f"审校师失败原因没写进证据段，片段：{seg[:180]}"
        # 关键补强：失败时**不许**渲染成"（无）"的空清单——那是把"没工作"演成"没发现"
        assert "（无）" not in seg, f"失败分支仍渲染出空清单占位（看起来像审过了、没发现问题）：{seg[:200]}"
        assert "没有证据" in seg, f"缺少「没有证据 ≠ 没有问题」这句显式声明：{seg[:200]}"

    def test_missing_text_says_so_instead_of_being_blank(self):
        """meta 没给原文时，要**如实写没给**，不许留个空段落让人以为"正文没问题"。"""
        out = build_report_html(_result(), {k: v for k, v in META.items() if k != "text"})
        assert "未随结果传入" in out, "缺原文时报告留白，没有如实标注"

    def test_coverage_derives_from_participating_paths(self):
        """覆盖率证据 = 参与了几路 / 一共三路。t7 没有现成 coverage 字段，必须从
        fusion.participating 推，且参与路数错了要能看出来。"""
        out = _html()
        assert "1/3" in out or "1 / 3" in out, f"报告没写明只有 1 路参与，实得片段：{out[:220]}"
        out2 = build_report_html(_result(fusion={"score": 10.0, "participating": ["rule", "perplexity", "reviewer"], "weights": {"rule": 0.5, "perplexity": 0.2, "reviewer": 0.3}, "detail": "三路参与", "caveat": CAVEAT}), dict(META))
        assert "3/3" in out2 or "3 / 3" in out2, "三路参与时覆盖率应显示 3/3"


# ================================================================ C 组：确定性


class TestDeterminism:
    def test_same_input_same_bytes(self):
        """固定输入产出**逐字节**稳定——否则 diff 两份报告毫无意义，
        而且说明实现里掺了 now()/随机数。"""
        a = _html()
        b = _html()
        assert a == b, "同样输入两次产出不一致（实现里不许出现时间戳/随机数）"

    def test_no_clock_or_randomness_imported(self):
        """🔴 碧霄自查补强：`test_same_input_same_bytes` **测不出时钟**。

        实测：把实现改成内部取 `datetime.now()`，两次调用相隔微秒、落在同一秒内，
        逐字节比较照样相等 → **14 passed 零覆盖**。这与批次一 C-1「契约变了、
        测试还留在退化区」是同一形状——判据看着有牙其实没牙。
        所以这里用 AST 直接钉"报告层不许 import 时间/随机源"，不靠时间运气。
        """
        import ast
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[1] / "app" / "deai_report.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        banned_modules = {"datetime", "time", "random", "uuid", "os"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update((a.name or "").split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        hit = imported & banned_modules
        assert not hit, f"报告层引入了不确定性来源 {sorted(hit)}——同一章两次报告必须可比对"

    def test_generated_at_comes_from_caller_not_clock(self):
        """时间戳若要显示，必须由调用方经 meta 传；没传就不显示。"""
        out = _html()
        assert "generated_at" not in out
        out2 = build_report_html(_result(), {**META, "generated_at": "2026-11-14 19:20"})
        assert "2026-11-14 19:20" in out2, "调用方给了时间戳应当显示"


# ================================================================ D 组：版式


class TestLayout:
    def test_section_order(self):
        out = _html()
        order = ["覆盖率", "信号表", "审校", "文体指标", "规则命中", "检测原文"]
        pos = [out.index(k) for k in order]
        assert pos == sorted(pos), f"版式顺序错了：{list(zip(order, pos))}"

    def test_caveat_is_at_the_top(self):
        """页首必须写明这句话（防止用户把风险分读成"有 82% 概率是 AI"）。"""
        out = _html()
        assert CAVEAT in out, "缺少「实验风险分不是 AI 生成概率」这句声明"
        assert out.index(CAVEAT) < out.index("信号表"), "这句话应出现在页首，不是埋在表格里"

    def test_signal_table_has_four_columns(self):
        """三路信号表四列齐备：状态 / 值 / 权重 / 说明。"""
        out = _html()
        seg = out[out.index("信号表") : out.index("审校")]
        for col in ("状态", "值", "权重", "说明"):
            assert col in seg, f"三路信号表缺「{col}」列"

    def test_rules_grouped_by_severity(self):
        out = _html()
        assert "high" in out.lower() or "严重" in out, "规则命中未按严重度分组"


# ================================================================ E 组：端点


class TestEndpoint:
    """`GET /api/ai-factory/projects/{id}/deai-report`。

    这组钉的是**花钱的开关默认关**：打开一份检测报告不该悄悄调模型。
    断言方式不用「参数 spy」，用**真调用有没有发生**——批次一 MJ-4 的教训是
    "参数传对了"和"事情真做了"之间没有桥（spy 只看参数、不走过路）。
    """

    def _run_endpoint(self, monkeypatch, *, chapter, content, with_reviewer=None, probe=None):
        import asyncio

        from app.routers import ai_extras as ex
        from app.models import AiProject, Chapter

        calls: list[str] = []

        async def fake_get_project(project_id, user, db):
            return AiProject(id=7, novel_id=1, summary_llm=None)

        async def fake_chat_text(config, system, prompt, **kw):
            calls.append("chat")
            return '{"ai_features": [], "human_evidence": []}'

        async def fake_pick_config(user, db, field):
            calls.append("config")
            return None

        class FakeDB:
            async def get(self, model, pk):
                return chapter if model.__name__ == "Chapter" else None if model.__name__ == "Chapter" else None

        monkeypatch.setattr(ex, "_get_project", fake_get_project)
        monkeypatch.setattr(ex, "_chat_text", fake_chat_text)
        monkeypatch.setattr(ex, "_pick_config", fake_pick_config)
        # content=None 的意思是「用 _mk_chapter 备好的正文」，不是「把正文清空」。
        # 写成无条件覆盖会把正文抹掉 → 端点如实报 400，看起来像实现错，其实是夹具错。
        if content is not None:
            chapter.content = content

        class U:
            id = 1

        async def _go():
            # 🔴 碧霄自查补强：原先**总是显式传**这两个开关，于是把默认值改成 True
            # 这个变异**20 passed 零覆盖**——测试绕过了它声称在测的默认值。
            # 现在 None = 「像浏览器那样不带查询参数」，让函数签名里的默认值真的生效。
            kwargs = {}
            if with_reviewer is not None:
                kwargs["with_reviewer"] = with_reviewer
            if probe is not None:
                kwargs["probe_logprobs"] = probe
            resp = await ex.deai_report_html(7, 99, user=U(), db=FakeDB(), **kwargs)
            # StreamingResponse 没有 .body，只能**迭代 body_iterator** 取证
            chunks = [c async for c in resp.body_iterator]
            return resp, b"".join(chunks).decode("utf-8")

        resp, text = asyncio.run(_go())
        return resp, text, calls

    def _mk_chapter(self):
        from app.models import Chapter

        ch = Chapter(id=99, novel_id=1, title="第十二章", sort_order=12)
        ch.content = "<p>他深吸一口气，内心翻涌着复杂情绪。</p>" * 20
        return ch

    def test_switch_defaults_are_off_in_signature(self):
        """两个花钱开关的**默认值必须是 False**（签名层直接钉，不靠调用运气）。"""
        import inspect

        from app.routers import ai_extras as ex

        sig = inspect.signature(ex.deai_report_html)
        for name in ("with_reviewer", "probe_logprobs"):
            assert name in sig.parameters, f"端点缺参数 {name}"
            assert sig.parameters[name].default is False, (
                f"{name} 默认值不是 False（实得 {sig.parameters[name].default!r}）——"
                "打开一份检测报告不该默认烧 token"
            )

    def test_default_switches_call_no_model(self, monkeypatch):
        """不带查询参数调用（等价浏览器直接打开链接）：一次模型都不该碰。"""
        resp, text, calls = self._run_endpoint(monkeypatch, chapter=self._mk_chapter(), content=None)
        assert calls == [], f"默认开关下不该碰模型配置与调用，实得 {calls}"
        assert "1/3" in text, f"默认只有规则路参与，报告必须写明 1/3，实得开头：{text[:160]}"

    def test_reviewer_switch_does_call_model(self, monkeypatch):
        resp, text, calls = self._run_endpoint(
            monkeypatch, chapter=self._mk_chapter(), content=None, with_reviewer=True
        )
        assert "chat" in calls, f"显式打开审校师却没调模型（开关是假的）：{calls}"

    @pytest.mark.parametrize(
        "own_novel, ch_novel, expect_ok",
        [
            (1, 1, True),    # 同项目：放行
            (1, 2, False),   # 绑了小说、拿别人的章：404
            (None, 2, False),  # 🔴 缺失档位（天演评审 C-1 critical）：项目没绑定小说
            (None, None, False),  # 两边都空也不许放行
        ],
        ids=["same-novel", "other-novel", "project-novel-is-None", "both-None"],
    )
    def test_chapter_ownership_checked_on_every_tier(self, monkeypatch, own_novel, ch_novel, expect_ok):
        """章节归属校验必须在**每个档位**都拦住，不许有短路分支。

        🔴 评审实锤（真库探针）：`if chapter is None or (p.novel_id and ...)` 里那个
        `p.novel_id and` 是**短路**——`AiProject.novel_id` 是可空列
        （ORM `Mapped[int | None]`、SQLite notnull=0，删掉绑定的小说时 FK 置空），
        于是 novel_id=None 的项目 + 任何别人的 chapter_id → **HTTP 200，
        报告里带着别人的整章正文**。我原先那条 404 测试只测了 novel_id=1 档位，
        None 档位**零覆盖**——判据在、档位落在退化区（批次一 C-1 同族）。

        语义定死：项目没绑定小说 = 它没有章节，**一律 404**，不是"跳过校验"。
        """
        import asyncio

        from fastapi import HTTPException

        from app.models import Chapter
        from app.routers import ai_extras as ex

        ch = Chapter(id=99, novel_id=ch_novel, title="别人的章", sort_order=12)
        ch.content = "<p>绝密正文：林昭的第二次开盒。</p>" * 8

        async def fake_get_project(project_id, user, db):
            from app.models import AiProject

            return AiProject(id=7, novel_id=own_novel, summary_llm=None)

        async def fake_chat_text(config, system, prompt, **kw):
            raise AssertionError("越权路径不该走到模型调用")

        class FakeDB:
            async def get(self, model, pk):
                if model.__name__ == "Chapter":
                    return ch
                return SimpleNamespace(id=1, username="u") if model.__name__ == "User" else None

        monkeypatch.setattr(ex, "_get_project", fake_get_project)
        monkeypatch.setattr(ex, "_chat_text", fake_chat_text)

        class U:
            id = 1

        async def _go():
            resp = await ex.deai_report_html(7, 99, user=U(), db=FakeDB())
            return b"".join([c async for c in resp.body_iterator]).decode("utf-8")

        if expect_ok:
            text = asyncio.run(_go())
            assert "绝密正文" in text, "自己的章节应当出报告"
        else:
            with pytest.raises(HTTPException) as ei:
                asyncio.run(_go())
            assert ei.value.status_code == 404, (
                f"档位 own={own_novel!r} ch={ch_novel!r} 应 404，实得 {ei.value.status_code}"
                "——跨用户章文泄露"
            )

    def test_empty_content_is_400_not_blank_report(self, monkeypatch):
        import asyncio

        import pytest
        from fastapi import HTTPException

        ch = self._mk_chapter()
        ch.content = "   <p></p>  "  # strip_html 之后是空的
        with pytest.raises(HTTPException) as ei:
            self._run_endpoint(monkeypatch, chapter=ch, content=None)
        assert ei.value.status_code == 400, f"空正文应 400，实得 {ei.value.status_code}"

    def test_response_is_html_attachment(self, monkeypatch):
        resp, text, _ = self._run_endpoint(monkeypatch, chapter=self._mk_chapter(), content=None)
        assert resp.media_type == "text/html; charset=utf-8", f"媒体类型错：{resp.media_type}"
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd and ".html" in cd, f"缺下载文件名：{cd}"


# ============================ 评审 FIX 轮新增判据（critical/major/minor）


class TestReviewFixes:
    """天演评审 B-2 / C-1 / C-3 / C1 四项的回归判据。"""

    def test_degenerate_status_is_shown_as_not_detected(self):
        """B-2 第三处：哨兵路必须在报告里显示"未真正检测"，
        否则前端只看三路表会以为规则路给了个真分。"""
        deg = {"signal": "rule", "status": "degenerate", "value": None,
               "detail": "文本过短，规则检测提前短路，此路未参与打分"}
        fused = {"score": None, "participating": [], "weights": {},
                 "detail": "无任何一路可用，无融合分", "caveat": CAVEAT}
        out = build_report_html(
            _result(rule=deg, perplexity=ds._skipped("perplexity") if hasattr(ds, "_skipped") else _result()["perplexity"], fusion=fused),
            {k: v for k, v in META.items() if k != "issues"},
        )
        assert "未真正检测" in out or "哨兵" in out, "degenerate 状态在报告里没有对应说法"
        assert "0/3" in out, f"无路参与时覆盖率应显示 0/3，实得片段：{out[out.index('覆盖率'):out.index('覆盖率')+80]}"

    def test_malformed_sections_degrade_to_labels_not_500(self):
        """C1 minor：`style` 是 list、`fusion` 是字符串、`reviewer.value` 是字符串时，
        报告**不该整页崩**（崩=打不开=等于没有报告），要降级成形态异常标注。"""
        bad = {
            "rule": {"signal": "rule", "status": "ok", "value": 50, "detail": "d"},
            "perplexity": {"signal": "perplexity", "status": "ok", "value": 1.0, "detail": "d"},
            "reviewer": {"signal": "reviewer", "status": "ok", "value": "字符串不是字典", "detail": "d"},
            "style": ["不是", "字典"],
            "fusion": "也不是字典",
        }
        out = build_report_html(bad, dict(META))
        assert "形态异常" in out, "畸形输入既没崩也没标注——静默吞掉是最坏的第三种"

    def test_rows_derive_from_signal_paths_not_hardcoded_three(self):
        """C3 minor：三路表与覆盖率必须**跟着实际信号路走**。
        结果里出现第四路时，报告不许把它**静默丢掉**（丢=谎报覆盖率）。"""
        four = _result()
        four["llm_judge"] = {"signal": "llm_judge", "status": "skipped", "value": None, "detail": "未接入"}
        out = build_report_html(four, dict(META))
        low = out.lower()
        assert "llm_judge" in low or "未识别" in out or "第四" in out, (
            "结果里在场的一路被报告静默省略了——表行是硬编码三行，将来加路必谎报"
        )

    def test_missing_quote_verified_defaults_to_unverified(self):
        """C1 附带：条目**缺 quote_verified 键**时不许当作"已验证"。
        默认值的方向必须是保守（未验证），不是乐观。"""
        rev = {"signal": "reviewer", "status": "ok",
               "value": {"ai_features": [{"quote": "一句没有核验标记的引文"}], "human_evidence": []},
               "detail": "d"}
        out = build_report_html(_result(reviewer=rev), dict(META))
        assert "未逐字命中" in out or "未核验" in out, "缺 quote_verified 被默认成了已验证（乐观默认）"

    def test_page_top_warning_bar_is_populated_after_render(self):
        """🔴 碧霄自查抓到自己刚写进去的顺序 bug：`warn_html` 曾被放在 sections
        字面量里、排在各表**之前**求值——而 notes 是渲染各表时才填的，
        于是警告条**永远是空的**，"形态异常"只散落在各节里，页首看着一片干净。
        这条钉的是：**页首警告条必须在各段渲染完之后才组装**。
        """
        bad = _result()
        bad["style"] = ["不是字典"]
        bad["llm_judge"] = {"signal": "llm_judge", "status": "skipped", "value": None, "detail": "未接入"}
        out = build_report_html(bad, dict(META))
        bars = re.findall(r'<div class="caveat">(.*?)</div>', out, re.S)
        assert len(bars) >= 2, f"页首只有免责句、没有形态警告条（实得 {len(bars)} 条）"
        joined = "".join(bars)
        assert "形态异常" in bars[1], f"第二条警告条内容不对：{bars[1][:120]}"
        assert "未登记" in joined, "未登记信号路没进页首警告条"
        # 警告条必须**在各节之前**（页首可见，不是埋在文体表里）
        assert out.index(bars[1]) < out.index("文体指标对照"), "形态警告条位置太靠后"


# ================================================================ u0c 补判据（L / N）
# 背景：独立评审用变异实验实测——把生产逻辑改坏，既有测试仍全绿。以下用例逐条补牙。
# 期望值全部硬编码，不从实现 import 常量当基准（本项目铁律）。


class TestU0cWarningBarIntegrity:
    """契约 L：页首警告条的**完整性**。

    既有 test_page_top_warning_bar_is_populated_after_render 只断言"警告条里出现了字"，
    没断言"该出现的警告都出现了"——把其中任意一类从汇总里摘掉，页面照样有别的字、照样绿。
    既有判据只说"警告条里有字"，本组钉的是"该有的每条都在"。
    🔴 实测事实（2026-11-16，逐行打开生产代码核过，非推测）：`build_report_html` 里
    `notes.append` 共 4 个点（deai_report.py:75 融合形态异常 / :115 未登记信号路 /
    :149 审校师证据形态异常 / :183 文体指标形态异常）。上一单实测时①是**死代码**
    （调用点 :249 把非字典 fusion 先过滤成 {} 再传下去 → :75 恒 False），同一份输入
    最多只能触发 2 条。**u0c-fix 已按裁决 A 修好 :249（原始值传给 _coverage_block）**，
    四条警告全部可达；本组判据随之从"缺陷形状快照"翻为正向完整性闸（4 条各恰一次 + 顺序）。
    ④审校师那条要求 reviewer.status=="ok"，与 ②③ 无冲突但和"三路都不理想"的夹具互斥。
    所以本组按**实际可得的形状**钉：多类异常同时喂进去时，页面上必须出现
    「应出现的每一条」且「每条恰出现一次」。
    """

    def test_every_kind_of_anomaly_has_its_own_label_not_one_generic_word(self):
        """既有判据 `test_malformed_sections_degrade_to_labels_not_500` 只断言了
        `"形态异常" in out` 这**四个字**——实测：把三类具体文案（未登记路 / 审校师证据 /
        文体指标）全删掉、只留融合那一句通用"形态异常"，那条老用例照样绿。
        所以这里钉的是**每条警告必须自带身份**：光有"形态异常"这个笼统词不算数。"""
        warn = re.findall(r'<div class="caveat">(.*?)</div>',
                          build_report_html(self._multi(), dict(META)), re.S)[1]
        assert warn.count("形态异常") == 3, f"应有且只有 3 处「形态异常」（融合 + 审校师 + 文体；未登记路那条不含此词），实得 {warn.count('形态异常')}：{warn}"
        for marker in self.WANT:
            assert marker in warn, f"缺少点名到路的警告「{marker}」：{warn[:220]}"

    def test_dead_fusion_note_is_emitted_when_fusion_is_not_a_dict(self):
        """✅ 绊马索已按裁决 A 翻成正向断言（u0c-fix）：上一单实测钉住
        deai_report.py:249 把非字典 fusion 先过滤成 {} 再传下去 → :75/:76 死代码。
        修法落地后（原始值传给 _coverage_block），喂 fusion="不是字典" 时：
        ①「融合结果形态异常（收到 str），无风险分」必须进页首警告条；
        ②覆盖率块必须走降级文案「融合数据形态异常」，不再谎报 0/3。"""
        r = _result()
        r["fusion"] = "不是字典"
        out = build_report_html(r, dict(META))
        bars = re.findall(r'<div class="caveat">(.*?)</div>', out, re.S)
        assert len(bars) == 2, f"应恰好 2 个 caveat div（免责句 + 警告条），实得 {len(bars)}"
        warn = bars[1]
        assert warn.count("融合结果形态异常") == 1, \
            f"融合形态异常警告必须恰出现 1 次，实得 {warn.count('融合结果形态异常')}：{warn[:250]}"
        assert "融合结果形态异常（收到 str）" in warn, \
            f"警告必须点名实际收到的类型：{warn[:250]}"
        assert "融合数据形态异常" in out, "覆盖率块必须走降级文案，不许静默显示 0/N"
        assert "0/3" not in out, "融合形态异常时覆盖率不该再假装是 0/3 的正常计数"

    def _multi(self):
        """一份同时踩到多条异常路的输入（实测可得的最大集合，见类文档）。"""
        r = _result()
        r["fusion"] = "不是字典"          # → ①融合形态异常（u0c-fix 修死代码后此条已可达）
        r["reviewer"] = {"signal": "reviewer", "status": "ok",
                         "value": "字符串不是字典", "detail": "d"}   # → ④审校师证据形态异常
        r["style"] = ["不是字典"]         # → ③文体形态异常
        r["llm_judge"] = {"signal": "llm_judge", "status": "skipped",
                          "value": None, "detail": "未接入"}         # → ②未登记路
        return r

    #: 硬编码：这份输入**应当**在警告条里出现的标记（每条恰一次）。
    #: u0c-fix：死代码修好后「融合结果形态异常」进入可达集合，WANT 由 3 条变 4 条。
    WANT = ("未登记在 _PATH_ORDER", "文体指标形态异常", "审校师证据形态异常",
            "融合结果形态异常")

    def test_every_expected_warning_appears_exactly_once(self):
        out = build_report_html(self._multi(), dict(META))
        bars = re.findall(r'<div class="caveat">(.*?)</div>', out, re.S)
        assert len(bars) == 2, f"应恰好 2 个 caveat div（免责句 + 警告条），实得 {len(bars)}"
        warn = bars[1]
        for marker in self.WANT:
            assert warn.count(marker) == 1, f"「{marker}」出现 {warn.count(marker)} 次（应恰 1）：{warn[:250]}"
        assert len(warn.split("；")) == 4, f"警告条应有 4 条，实得 {len(warn.split('；'))}：{warn}"

    def test_dropping_any_single_warning_turns_red(self):
        """完整性判据的另一面：**逐条点名**，摘掉任何一条都过不了（既有测试只查"有字"）。"""
        warn = re.findall(r'<div class="caveat">(.*?)</div>',
                          build_report_html(self._multi(), dict(META)), re.S)[1]
        got = {m for m in self.WANT if m in warn}
        want = set(self.WANT)
        assert got == want, f"警告条不完整，缺失：{want - got}"
        # 顺序闸按**实际 append 次序**钉（实测：_coverage_block:75 最先，随后
        # _signals_table:115 早于 _reviewer_block:149，后者又早于 _style_table:183）
        # ——顺序被改坏说明各段渲染次序动了。u0c-fix：融合条目由死变活，加入序列首位。
        order = ("融合结果形态异常", "未登记在 _PATH_ORDER", "审校师证据形态异常", "文体指标形态异常")
        idx = [warn.index(m) for m in order]
        assert idx == sorted(idx), f"警告条目顺序错乱：{idx}｜{warn[:200]}"


class TestU0cUnknownStatusTripwire:
    """契约 N：未知状态的标签文案兜底。绊马索性质，见各用例注释。"""

    # 🔴 绊马索（AGENTS.md 明文要求就地注明）：**故意**用当前代码不该认识的状态串
    # （"frobnicated" 不在 _STATUS_LABEL 的五键里）。一旦有人删掉兜底分支或改成
    # KeyError/静默按 ok 渲染，这条立刻变红——它守的是"未来不许扩大状态枚举面而不给说法"。
    def test_unknown_status_falls_back_without_crashing_and_shows_raw_value(self):
        r = _result()
        r["reviewer"]["status"] = "frobnicated"
        out = build_report_html(r, dict(META))  # 不许抛异常
        assert "未知状态（frobnicated）" in out, "未知状态没有显形成「未知状态（xxx）」"
        seg = out[out.index("审校师证据"):]
        assert "正常参与打分" not in seg, "未知状态被静默按 ok 渲染了"

    # 🔴 绊马索同上：用最恶劣输入钉"原始串不许泄进 class 属性"（防属性逃逸）。
    def test_unknown_status_raw_string_never_leaks_into_class_attr(self):
        r = _result()
        r["rule"]["status"] = 'x" onload="alert(1)'
        out = build_report_html(r, dict(META))
        assert 'class="st-x" onload=' not in out, "原始状态串未经转义就进了 class 属性（可执行入口）"
        assert "&quot;" in out or "&#x27;" in out or '"' not in out.split('class="')[1][:40], \
            "状态属性值应被转义封闭"

    # 🔴 绊马索：文体表同样喂不存在的状态串，钉兜底覆盖到 style 一路。
    def test_unknown_style_status_also_gets_explicit_label(self):
        r = _result()
        r["style"] = {"疲劳词密度": {"value": 3.2, "human_low": None, "human_high": None,
                                      "deviation": None, "status": "not_a_real_status"}}
        out = build_report_html(r, dict(META))
        assert "未知状态（not_a_real_status）" in out, "文体表遇到未知状态没有走兜底"
