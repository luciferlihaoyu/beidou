"""t4-schema 产物契约校验层测试（纯逻辑，无网络、无路由）。

背景：`ai_factory.py` 的 `_parse_json` 之后是"手工摸字段"，字段名拼错、类型不对、
多一层少一层全靠运气。本测试为三类产物立契约（TASK-t4-schema.md §3.3），
并用**从 ai_factory.py 原样抄录的 legacy 校验逻辑**锁住等价性：
旧代码拒绝（502）的输入，新 schema 必须也拒绝——不许把 502 变成 200。

============================================================================
真实消费字段表（证据）
============================================================================
以下键路径逐一 grep 自 `app/routers/ai_factory.py`（t4 动工时 3076 行版本，
_parse_json 之后到落库为止的全部读取点；行号随代码漂移，以本表为快照）：

init 节点（BookSpecOut，L1007 起 `spec`）：
- （根须为 dict）      L1010 isinstance(spec, dict)
- premise             L1010 spec.get("premise") truthy 硬校验（唯一硬闸）；
                      L1014-1016 kept 覆盖写；L1024 _default_kb_query 读取
                      （须 str 且 strip 非空，L901-904）
- titles              L1018-1019 isinstance list 检查后重排（截 3 个）；
                      _default_kb_query L894-898（titles[0] 须 str 非空）
- genre               L1021-1022 truthy → str()[:50] 入 p.genre；
                      _default_kb_query L899-900（须 str 非空）
- theme               _default_kb_query L901-904（须 str 非空）
- time/place/tone/pov L1014-1016 仅 kept 覆盖写，无读取点；prompt 契约
                      L992-999；经 L1020 book_spec_json 整体快照透传
- init prompt 另要求 characters（L999），但代码零读取 → 不进 schema

setup 节点（SettingOut，L1127 起 `data`）：
- characters               L1131 get or []；L1133-1134 空则 502「AI 未生成角色」；
                           L1148 [:12]
- characters[].name        L1152 str(c.get("name",""))[:100]
- characters[].role        L1153 str(c.get("role","配角"))[:50]
- characters[].description L1154 str(c.get("description",""))[:2000]
- characters[].arc         L1155 truthy 时拼接进 description
- worldview                L1132 get or []（**无空校验**，空=合法）；L1158 [:20]
- worldview[].category     L1162 str(w.get("category","其他"))[:50]
- worldview[].title        L1163 str(w.get("title",""))[:200]
- worldview[].content      L1164 str(w.get("content",""))[:5000]

outline 节点（OutlineOut，L1230 起 `data`）：
- volumes                  L1233 get or []；L1234-1235 空→502「AI 未生成大纲」；
                           L1256 [:20]
- volumes[].title          L1257 str(v.get("title", f"第{vi+1}卷"))[:200]
- volumes[].chapters       L1260 (v.get("chapters") or [])[:100]
                           （主体路径不查全局章数——0 章大纲会静默落库，
                            任务书 D2 钦定 schema 补"至少 1 章"闸）
- volumes[].chapters[].title    L1264 str(ch.get("title",""))[:200]
- volumes[].chapters[].outline  L1276 str(ch.get("outline",""))[:2000]
- （data 整体 L1281 json.dumps 进 p.outline_json 快照；summary 无读取点，
   经快照透传）

outline_continue 节点（OutlineOut，L1388/1393 起 `data`）：
- volumes                  L1396 (data or {}).get("volumes") or []，isinstance
                           dict 过滤后 [:10]；L1398-1399 空/planned=0 →
                           502「AI 未产出任何续写章节」
- volumes[].chapters       L1397 planned 求和（无类型过滤）；L1405 isinstance
                           dict 过滤 [:40]；L1406-1407 空卷跳过
- volumes[].title          L1410 str(v.get("title", f"第{base_vol_sort+vi+1}卷"))[:200]
- volumes[].chapters[].title    L1419 str(c.get("title",""))[:200]
- volumes[].chapters[].outline  L1431 str(c.get("outline",""))[:2000]
- L1435-1437 added==0 → 502「AI 返回的大纲没有可用章节」（continue 路径的
  第二道闸：planned>0 但章元素全非 dict 时触发）

任务书 D2 猜测字段对照：protagonist / world / selling_points / number / synced
**代码里完全不存在**——schema 只认上表，一个不猜。

运行：PYTHONPATH=. pytest tests/test_schemas.py -q
"""

import ast
import inspect

import pytest

from app import schemas
from app.schemas import OutlineOut, BookSpecOut, SettingOut, validate

# ---------------------------------------------------------------------------
# _legacy_check_*：从 ai_factory.py 原样抄录的既有手工校验逻辑（参考实现）。
# 用途：锁住「旧代码 502 的输入，新 schema 必须也拒绝」的等价性（任务书 §3.3
# 最重要的回归护栏）。行号为 t4 动工时快照；try 包裹仅把旧代码的
# AttributeError/TypeError 崩溃（FastAPI 500）归一为"拒绝"——崩溃同样不入库，
# 语义上等价于拒绝，且新 schema 会把它们变成优雅 502（改进而非放宽）。
# ---------------------------------------------------------------------------


def _legacy_check_init(spec):
    """ai_factory.py L1010-1011 原样抄录。"""
    if not isinstance(spec, dict) or not spec.get("premise"):
        return "AI 输出缺少核心字段（premise）"
    return None


def _legacy_check_setup(data):
    """ai_factory.py L1131-1134（502 闸）+ L1148-1164（落库段访问面）原样抄录。

    拒绝入库的完整语义 = 502 或崩溃：L1131 的 502 闸对 truthy 非数组（如
    "abc"）放行，但 L1148 `for c in chars[:12]` 遍历字符串字符时 c.get
    AttributeError → FastAPI 500 崩溃——同样不入库。因此落库段的元素访问
    面必须一并抄入，否则 legacy 会把"旧代码实际崩溃"的输入误判为放行。
    """
    try:
        chars = data.get("characters") or []
        if not chars:
            return "AI 未生成角色"
        world = data.get("worldview") or []
        for c in chars[:12]:  # L1148-1155 访问面
            c.get("name", "")
            c.get("role", "配角")
            c.get("description", "")
            c.get("arc")
        for w in world[:20]:  # L1158-1164 访问面
            w.get("category", "其他")
            w.get("title", "")
            w.get("content", "")
        return None
    except (AttributeError, TypeError):
        return "旧代码 AttributeError/TypeError 崩溃（=拒绝）"


def _legacy_check_outline(data):
    """ai_factory.py L1233-1235（502 闸）+ L1256-1276（落库段访问面）原样抄录。

    同 _legacy_check_setup：闸对 truthy 非数组放行，L1256 `volumes_data[:20]`
    对字符串切片后逐字符 v.get 崩溃（500）——崩溃=拒绝，落库段一并抄。
    """
    try:
        volumes_data = data.get("volumes") or []
        if not volumes_data:
            return "AI 未生成大纲"
        for v in volumes_data[:20]:  # L1256-1276 访问面
            v.get("title")
            for ch in (v.get("chapters") or [])[:100]:
                ch.get("title", "")
                ch.get("outline", "")
        return None
    except (AttributeError, TypeError):
        return "旧代码 AttributeError/TypeError 崩溃（=拒绝）"


def _legacy_check_outline_continue(data):
    """ai_factory.py L1396-1399 + L1404-1407 + L1435-1437 原样抄录。

    L1396-1399：volumes（dict 过滤）空或 planned<=0 → 「AI 未产出任何续写章节」
    L1435-1437：added==0（章元素全非 dict）→ 「AI 返回的大纲没有可用章节」
    """
    try:
        volumes_data = [v for v in ((data or {}).get("volumes") or []) if isinstance(v, dict)][:10]
        planned = sum(len(v.get("chapters") or []) for v in volumes_data)
        if not volumes_data or planned <= 0:
            return "AI 未产出任何续写章节"
        added = 0
        for v in volumes_data:
            added += len([c for c in (v.get("chapters") or []) if isinstance(c, dict)])
        if added == 0:
            return "AI 返回的大纲没有可用章节"
        return None
    except (AttributeError, TypeError):
        return "旧代码 AttributeError/TypeError 崩溃（=拒绝）"


# ---------------------------------------------------------------------------
# 等价性双向差集（本阶段核心护栏）
# ---------------------------------------------------------------------------


def _assert_equivalence(node: str, legacy_fn, payloads: list[tuple[str, object]]) -> None:
    """对同一批输入断言：legacy 拒 ⇒ 新拒；legacy 放 ⇒ 新放。

    正向差集（legacy 拒 ∩ 新放）必须为空——不许把 502 降级成 200；
    反向差集（legacy 放 ∩ 新拒）必须为空——不许误杀旧代码接受的产物。
    （任务书 D2 钦定的三点收紧不进本集，见 TestD2MandatedTightening。）
    """
    for name, payload in payloads:
        legacy_verdict = legacy_fn(payload)
        _obj, errors = validate(node, payload)
        if legacy_verdict is not None:
            assert errors, f"[{node}/{name}] legacy 拒绝（{legacy_verdict}）但新 schema 放行——502 被降级！"
        else:
            assert not errors, f"[{node}/{name}] legacy 放行但新 schema 拒绝：{errors}"


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

_SETUP_OK = {
    "characters": [
        {"name": "林越", "role": "主角", "description": "沉默寡言的少年", "arc": "从懦弱到担当"},
        {"name": "苏挽", "role": "对手", "description": "名门嫡女，锋芒毕露"},
    ],
    "worldview": [
        {"category": "力量体系", "title": "灵脉", "content": "天地灵气自九脉而出"},
    ],
}

_OUTLINE_OK = {
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
}


class TestLegacyEquivalence:
    """逐节点：legacy 拒绝 ⇒ 新拒绝；legacy 放行 ⇒ 新放行（双向差集为空）。"""

    def test_init_equivalence(self):
        payloads = [
            ("全空dict", {}),
            ("缺premise其余齐", {k: v for k, v in _INIT_OK.items() if k != "premise"}),
            ("premise为None", {**_INIT_OK, "premise": None}),
            ("premise为空串", {**_INIT_OK, "premise": ""}),
            ("根为字符串", "not-a-dict"),
            ("根为数组", ["list"]),
            ("根为None", None),
            ("正常产物", _INIT_OK),
            ("只有premise", {"premise": "唯一硬闸字段，其余全缺也应放行"}),
        ]
        _assert_equivalence("init", _legacy_check_init, payloads)

    def test_setup_equivalence(self):
        payloads = [
            ("全空dict", {}),
            ("缺characters", {"worldview": []}),
            ("characters为None", {"characters": None, "worldview": None}),
            ("characters为空数组", {"characters": [], "worldview": []}),
            ("characters为字符串", {"characters": "abc"}),
            ("根为字符串", "abc"),
            ("根为None", None),
            # 注意：「角色缺 name」不进本差集——旧代码确实放行（str("") 落库
            # 无名角色），新 schema 拒绝它是任务书 §3.1 钦定收紧②，
            # 见 TestD2MandatedTightening 与 TestNegativeMatrix。
            ("正常产物", _SETUP_OK),
            ("无worldview", {"characters": [{"name": "林越"}]}),
            ("worldview为None", {"characters": [{"name": "林越"}], "worldview": None}),
            ("worldview为空数组", {"characters": [{"name": "林越"}], "worldview": []}),
            # 旧代码对 None 字段值会 str(None)="None" 落库（脏值）；新规整对象保留 None——
            # 两侧都放行，落库质量由规整对象提升，拒绝面不变
            ("worldview字段值为None", {"characters": [{"name": "林越"}], "worldview": [{"category": None, "title": "灵脉", "content": None}]}),
        ]
        _assert_equivalence("setup", _legacy_check_setup, payloads)

    def test_outline_equivalence(self):
        payloads = [
            ("全空dict", {}),
            ("缺volumes", {"other": 1}),
            ("volumes为None", {"volumes": None}),
            ("volumes为空数组", {"volumes": []}),
            ("volumes为dict", {"volumes": {}}),
            ("volumes为字符串", {"volumes": "x"}),
            ("根为None", None),
            ("正常产物", _OUTLINE_OK),
            ("卷缺title与summary", {"volumes": [{"chapters": [{"title": "第一章", "outline": "要点"}]}]}),
            ("章缺title与outline", {"volumes": [{"title": "卷", "chapters": [{}]}]}),
        ]
        _assert_equivalence("outline", _legacy_check_outline, payloads)

    def test_outline_continue_equivalence(self):
        payloads = [
            ("全空dict", {}),
            ("volumes为空数组", {"volumes": []}),
            ("volumes全非dict", {"volumes": [123, "卷"]}),
            ("章元素全非dict-1437闸", {"volumes": [{"chapters": ["第一章", "第二章"]}]}),
            ("volumes为None", {"volumes": None}),
            ("volumes为字符串", {"volumes": "abc"}),  # L1396 列表推导遍历字符→全非dict→502
            ("根为None", None),
            ("根为字符串", "abc"),
            ("正常续写产物", _OUTLINE_OK),
            ("空卷+有章卷混排", {"volumes": [{"title": "新卷", "chapters": []}, {"title": "续卷", "chapters": [{"title": "第十一章", "outline": "接续"}]}]}),
        ]
        _assert_equivalence("outline_continue", _legacy_check_outline_continue, payloads)


class TestD2MandatedTightening:
    """任务书 D2 钦定、比旧代码更严的三点收紧（旧放行、新拒绝）。

    这些输入不进上面的等价差集（差集会红），但必须单独钉住：
    ① OutlineOut 全局至少 1 章——旧 outline 主体对 0 章大纲静默落库；
    ② 每个角色必须有非空 name——旧代码 str("") 直接落库无名角色；
    ③ 数组元素级类型检查——旧代码对非 dict 元素直接 AttributeError 500 崩溃
      （崩溃也拒绝，等价；但"title 数字落库成 '1'"这类旧代码真放行的，新拒绝）。
    """

    def test_zero_chapter_outline_rejected_on_main_path(self):
        """0 章大纲：旧主体静默落库（total_chapters=0 状态推到 writing），
        新 schema 拒绝——补上 continue 路径 L1437 语义在主路径的洞。"""
        _obj, errors = validate("outline", {"volumes": [{"title": "空卷", "chapters": []}]})
        assert errors, "0 章大纲必须被拒绝（D2：至少 1 卷且至少 1 章）"

    def test_character_empty_name_rejected(self):
        _obj, errors = validate("setup", {"characters": [{"name": ""}]})
        assert errors, "空 name 角色必须被拒绝（任务书 §3.1：每个有 name）"

    def test_chapter_scalar_title_rejected(self):
        """数字章名旧代码 str(1)='1' 落库；新 schema 拒绝（类型契约）。"""
        _obj, errors = validate(
            "outline", {"volumes": [{"title": "卷", "chapters": [{"title": 1, "outline": 2}]}]}
        )
        assert errors, "章 title/outline 类型错必须被拒绝"
        assert any("title" in e for e in errors)


class TestFourStates:
    """四态：正常 / 缺字段 / 类型错 / 多余字段（多余必须忽略而非拒收）。"""

    def test_happy_init_returns_object(self):
        obj, errors = validate("init", _INIT_OK)
        assert errors == []
        assert isinstance(obj, BookSpecOut)
        assert obj.premise == _INIT_OK["premise"]
        assert obj.titles == ["书名A", "书名B"]

    def test_happy_setup_returns_object(self):
        obj, errors = validate("setup", _SETUP_OK)
        assert errors == []
        assert isinstance(obj, SettingOut)
        assert obj.characters[0].name == "林越"
        assert obj.worldview[0].title == "灵脉"

    def test_happy_outline_and_continue_share_model(self):
        for node in ("outline", "outline_continue"):
            obj, errors = validate(node, _OUTLINE_OK)
            assert errors == [], f"{node} 正常产物不应报错"
            assert isinstance(obj, OutlineOut)
            assert obj.volumes[0].chapters[1].outline == "主角被迫离村，踏上西行路"

    def test_extra_fields_ignored_not_rejected(self):
        """模型自造字段很常见：必须放行，且规整对象上不残留。"""
        payload = {
            **_OUTLINE_OK,
            "mood": "昂扬",
            "word_budget": 3000,
            "volumes": [{**_OUTLINE_OK["volumes"][0], "notes": "自造卷级字段"}],
        }
        obj, errors = validate("outline", payload)
        assert errors == [], f"多余字段不该拒收：{errors}"
        dump = obj.model_dump()
        assert "mood" not in dump and "word_budget" not in dump
        assert "notes" not in dump["volumes"][0]

    def test_failure_returns_none_and_errors(self):
        obj, errors = validate("init", {})
        assert obj is None
        assert errors, "失败时错误列表必须非空"

    def test_unknown_node_rejected(self):
        """任务书 §3.2：review/synopsis 等不接——validate 必须防御未知节点。"""
        obj, errors = validate("review", {"issues": []})
        assert obj is None
        assert errors
        assert "review" in errors[0]


class TestNegativeMatrix:
    """负例档位矩阵：每类缺陷至少两种档位，证明判据不是单点碰巧有效。"""

    @pytest.mark.parametrize(
        "payload",
        [
            {},  # 缺全部字段
            {"genre": "玄幻"},  # 只缺 premise（缺 1 个关键字段）
            {"premise": None},  # 值为 None
            {"premise": ""},  # 值为空串
            {"premise": 123},  # 类型错
            {"premise": ["数组梗概"]},  # 类型错（容器）
            "字符串",  # 根类型错
            42,  # 根类型错（数字）
            ["数组"],  # 根类型错（列表）
        ],
        ids=["空dict", "缺premise", "None", "空串", "数字", "数组值", "根串", "根数字", "根数组"],
    )
    def test_init_negatives(self, payload):
        obj, errors = validate("init", payload)
        assert obj is None
        assert errors, f"档位 {payload!r} 必须被拒绝"

    @pytest.mark.parametrize(
        "characters",
        [
            None,  # 键值为 None
            [],  # 空数组
            "abc",  # 类型错（标量）
            {},  # 类型错（对象）
            [{"role": "主角"}],  # 嵌套缺一层（缺 name）
            [{"name": None}],  # 嵌套值为 None
            [{"name": ""}],  # 嵌套空串
            [{"name": 123}],  # 嵌套类型错
            [{"name": "甲"}, {"name": "乙"}, {"role": "丙"}],  # 第 3 个缺 name
            [123],  # 元素整体非对象
            [{"name": "甲"}, 456],  # 混排：第 2 个非对象
        ],
        ids=["None", "空数组", "标量", "对象", "缺name", "None名", "空串名", "数字名", "第3个缺name", "元素非对象", "混排非对象"],
    )
    def test_setup_characters_negatives(self, characters):
        obj, errors = validate("setup", {"characters": characters, "worldview": []})
        assert obj is None
        assert errors, f"characters 档位 {characters!r} 必须被拒绝"

    @pytest.mark.parametrize(
        "volumes",
        [
            None,
            [],
            {},
            "第一卷",
            [{"title": "只有卷没有章"}],  # 嵌套缺一层
            [{"title": "卷", "chapters": None}],  # chapters 为 None
            [{"title": "卷", "chapters": []}],  # chapters 空数组
            [{"chapters": [{"title": "章", "outline": "要点"}]}],  # 卷缺 title（可选）→ 应放行
        ],
        ids=["None", "空数组", "对象", "标量", "卷无chapters", "chaptersNone", "chapters空", "卷缺title有章(放行)"],
    )
    def test_outline_volumes_negatives(self, volumes):
        # 最后一个档位：volumes=[无章卷, 有章卷] —— 全局合计 1 章，应放行；
        # 其余全拒。在这里显式分流，避免参数化里藏一个假负例。
        _obj, errors = validate("outline", {"volumes": volumes})
        if volumes == [{"chapters": [{"title": "章", "outline": "要点"}]}]:
            assert not errors, f"全局合计 1 章的产物应放行：{errors}"
        else:
            assert errors, f"volumes 档位 {volumes!r} 必须被拒绝"
            # 注意：volumes=[{"chapters":[{"title":"章"}]}] 全局 1 章应放行——
            # 该档位构造为"唯一卷 0 章"，见上面 ids。

    def test_outline_mixed_volumes_with_one_valid_chapter_passes(self):
        """空卷 + 有章卷混排：全局 ≥1 章即放行（与 continue 的 planned 语义一致）。"""
        payload = {
            "volumes": [
                {"title": "空卷", "chapters": []},
                {"title": "有货卷", "chapters": [{"title": "第一章", "outline": "要点"}]},
            ]
        }
        obj, errors = validate("outline", payload)
        assert errors == [], f"空卷混排不该拒收：{errors}"

    def test_worldview_is_loose(self):
        """worldview 无空校验（旧代码 L1132 无闸）：None/缺/空数组全放行。"""
        base = {"characters": [{"name": "林越"}]}
        for extra in ({}, {"worldview": None}, {"worldview": []}):
            obj, errors = validate("setup", {**base, **extra})
            assert errors == [], f"worldview 档位 {extra} 不该拒收：{errors}"

    @pytest.mark.parametrize(
        "bad_worldview",
        ["字符串", 123, [123], [{"title": 1}], [[{"title": "嵌套数组"}]]],
        ids=["标量", "数字", "元素非对象", "title类型错", "元素是数组"],
    )
    def test_worldview_bad_element_types_rejected(self, bad_worldview):
        obj, errors = validate("setup", {"characters": [{"name": "林越"}], "worldview": bad_worldview})
        assert obj is None
        assert errors


class TestMessageShape:
    """D3 文案形状：字段路径 + 要什么 + 实际是什么；全中文；硬编码期望串。

    注意：本类的期望文案**全部硬编码**，不从 schemas 的任何表里读取——
    读实现的断言 = 实现改它也跟着改 = 护栏是空的。
    """

    def _errors(self, node, payload):
        _obj, errors = validate(node, payload)
        assert errors, "该负例必须产生错误"
        return errors

    def test_premise_missing_message_exact(self):
        errors = self._errors("init", {"genre": "玄幻"})
        assert "premise：缺少核心梗概（这是整本书的梗概，不能空）" in errors

    def test_premise_empty_string_message_exact(self):
        errors = self._errors("init", {"premise": ""})
        assert "premise：不能为空串（核心梗概必须有实际内容）" in errors

    def test_premise_wrong_type_message_exact(self):
        errors = self._errors("init", {"premise": 123})
        assert "premise：应为字符串，实际是 int" in errors

    def test_characters_list_type_message_exact(self):
        errors = self._errors("setup", {"characters": "abc"})
        assert "characters：应为数组，实际是 str" in errors

    def test_characters_empty_message_exact(self):
        errors = self._errors("setup", {"characters": []})
        assert "characters：不能为空数组（至少需要 1 项）" in errors

    def test_nested_name_missing_message_exact(self):
        """D3 下标路径形式：characters[2].name + 第 3 个角色。"""
        errors = self._errors("setup", {"characters": [{"name": "甲"}, {"name": "乙"}, {"role": "丙"}]})
        assert "characters[2].name：第 3 个角色缺少角色名" in errors

    def test_root_not_dict_message_exact(self):
        errors = self._errors("init", "not-a-dict")
        assert "输入整体：应为 JSON 对象（大括号包裹），实际是 str" in errors

    def test_volume_element_not_dict_message_exact(self):
        errors = self._errors("outline", {"volumes": [123]})
        assert "volumes[0]：应为 JSON 对象（大括号包裹），实际是 int" in errors

    def test_zero_chapter_message_exact(self):
        errors = self._errors("outline", {"volumes": [{"title": "空卷", "chapters": []}]})
        assert "volumes：至少要包含 1 个章节（全书合计章数不能为 0）" in errors

    def test_no_english_pydantic_phrases_anywhere(self):
        """禁止 pydantic 英文原文漏出（D3 红线）。类型名（str/int）是豁免的技术标识符。"""
        bad_phrases = [
            "should be",
            "Input should",
            "Expected",
            "expected",
            "valid string",
            "Field required",
            "field required",
            "unable to",
            "Unable to",
        ]
        probes = [
            ("init", {}),
            ("init", {"premise": 123}),
            ("init", ["数组"]),
            ("setup", {"characters": [{"name": {"嵌套对象": 1}}]}),
            ("setup", {"characters": [123]}),
            ("outline", {"volumes": "x"}),
            ("outline", {"volumes": [{"title": "卷", "chapters": [None]}]}),
        ]
        for node, payload in probes:
            _obj, errors = validate(node, payload)
            blob = "；".join(errors)
            for bad in bad_phrases:
                assert bad not in blob, f"[{node}/{payload!r}] 文案漏出英文原句「{bad}」：{blob}"


class TestTruncation:
    """D3：单次最多 8 条，追加「其余 N 条同类问题已省略」。"""

    def test_more_than_8_errors_truncated_exact(self):
        payload = {"characters": [{"role": "炮灰"} for _ in range(30)]}  # 30 个全缺 name
        _obj, errors = validate("setup", payload)
        assert len(errors) == 9, f"应恰好 8 条+1 条省略提示，实际 {len(errors)}：{errors}"
        assert errors[-1] == "…（其余 22 条同类问题已省略）"

    def test_omitted_count_matches_total(self):
        payload = {"characters": [{"role": "炮灰"} for _ in range(12)]}  # 12 条 → 省略 4
        _obj, errors = validate("setup", payload)
        assert errors[-1] == "…（其余 4 条同类问题已省略）"

    def test_exactly_8_not_truncated(self):
        payload = {"characters": [{"role": "炮灰"} for _ in range(8)]}
        _obj, errors = validate("setup", payload)
        assert len(errors) == 8
        assert all("省略" not in e for e in errors)

    def test_different_error_kinds_count_toward_same_budget(self):
        """截断预算跨错误种类共享：6 空名（string_too_short）+ 5 缺名（missing）
        + 1 条 worldview 类型错 = 12 条 → 截 8 + 省略 4。"""
        payload = {
            "characters": [{"name": ""} for _ in range(6)] + [{"role": "x"} for _ in range(5)],
            "worldview": "坏类型",
        }
        _obj, errors = validate("setup", payload)
        assert len(errors) == 9
        assert errors[-1].startswith("…（其余 ")


class TestHumanize:
    """_humanize 对未知错误类型不抛、不漏英文（pydantic 升级兜底）。"""

    class _FakeExc:
        def __init__(self, errors):
            self._errors = errors

        def errors(self):
            return self._errors

    def test_unknown_error_type_falls_back_to_chinese(self):
        errors = schemas._humanize(
            self._FakeExc(
                [
                    {
                        "type": "quantum_mismatch_from_future_pydantic",
                        "loc": ("characters", 0, "name"),
                        "msg": "Alien input should be quantum",
                        "input": "whatever",
                    }
                ]
            )
        )
        assert errors == ["characters[0].name：字段格式不符合契约要求，请检查它的类型与结构"]

    def test_unknown_error_type_no_english_leak(self):
        errors = schemas._humanize(
            self._FakeExc([{"type": "xyz_unknown", "loc": ("a",), "msg": "totally english here", "input": 1}])
        )
        blob = "；".join(errors)
        assert "english" not in blob
        assert "should" not in blob

    def test_empty_errors_gives_empty_list(self):
        assert schemas._humanize(self._FakeExc([])) == []


class TestD1Purity:
    """D1 分层：schemas 不 import FastAPI、不 raise HTTPException、不 import app.models。"""

    def _tree_and_source(self):
        source = inspect.getsource(schemas)
        return ast.parse(source), source

    def test_no_fastapi_import(self):
        tree, _src = self._tree_and_source()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            for mod in mods:
                assert not mod.startswith("fastapi"), f"schemas 不许 import {mod}（D1：纯校验层）"

    def test_no_httpexception_literal(self):
        """AST 级检查：代码里不许出现 HTTPException 名字（docstring 说明文字不算）。"""
        tree, _src = self._tree_and_source()
        offenders = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and "HTTPException" in n.id]
        offenders += [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and "HTTPException" in n.attr]
        assert not offenders, f"schemas 代码不许引用 HTTPException（D1）：{offenders}"

    def test_no_app_models_import(self):
        """任务书 §3.1：不 import app.models（避免循环依赖）。"""
        tree, _src = self._tree_and_source()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
                raise AssertionError(f"schemas 不许 import {node.module}（只许 pydantic/标准库）")
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not a.name.startswith("app."), f"schemas 不许 import {a.name}"

    def test_validate_signature_pure(self):
        """返回 (obj|None, errors) 二元组，不抛 HTTP 异常——router 才有权变 502。"""
        result = validate("init", {})
        assert isinstance(result, tuple) and len(result) == 2


class TestMutationSentinels:
    """反向证明（防自证式断言）：把必填改回"不校验"必须红。

    静态形式：关键字段必须 is_required / 带空壳约束；
    动态形式：全部负例矩阵（上面 TestNegativeMatrix / TestLegacyEquivalence）
    本身就是变异哨兵——任何一条放宽都会红。
    """

    def test_premise_is_required_field(self):
        info = BookSpecOut.model_fields["premise"]
        assert info.is_required(), "premise 必须无默认值（必填）——这条红说明 premise 被改回可选"

    def test_volumes_is_required_field(self):
        info = OutlineOut.model_fields["volumes"]
        assert info.is_required(), "volumes 必须必填——这条红说明大纲空壳闸被拆除"

    def test_characters_is_required_field(self):
        info = SettingOut.model_fields["characters"]
        assert info.is_required(), "characters 必须必填——这条红说明角色空壳闸被拆除"

    def test_extra_is_ignore_not_forbid(self):
        """D2：extra 必须是 ignore（多给字段不拒收），不许是 forbid/strict。"""
        for model in (BookSpecOut, SettingOut, OutlineOut):
            assert model.model_config.get("extra") == "ignore", f"{model.__name__} 的 extra 必须是 ignore"

    def test_node_models_wiring(self):
        """任务书 §3.1 钦定的 NODE_MODELS 映射。"""
        assert schemas.NODE_MODELS == {
            "init": BookSpecOut,
            "setup": SettingOut,
            "outline": OutlineOut,
            "outline_continue": OutlineOut,
        }



# ===========================================================================
# 裁决三（碧霄拍板：公式 B 嵌套保真 + C 滤新增 None）：快照合并已是产品代码
# app/schemas.py::merge_snapshot。测试钉住最终行为；测试内保留字面公式 A
# 做对照（差异实锤），防止将来有人"简化"回 A 而无人察觉。
# ===========================================================================


def _merge_literal(data: dict, obj) -> dict:
    """公式 A（已否决的字面公式，仅作对照）：{**原始 data, **规整后字段}。"""
    return {**data, **obj.model_dump()}


class TestMergeSnapshotPolicy:
    """merge_snapshot（公式 B+C）最终行为：规整覆盖 / 嵌套保真 / 滤新增 None / 超长告警。"""

    def test_top_level_extra_fields_survive(self):
        """顶层自造字段必须保住——裁决三理由 1 的字面要求。"""
        payload = {**_OUTLINE_OK, "mood": "昂扬", "word_budget": 3000}
        obj, errors = validate("outline", payload)
        assert errors == []
        merged = schemas.merge_snapshot(payload, obj)
        assert merged["mood"] == "昂扬" and merged["word_budget"] == 3000

    def test_init_characters_survives(self):
        """裁决二定案：init 的 characters（schema 未定义）必须原样保留。

        前端 FactoryProject.tsx:390-399 渲染 spec.characters（name/role/brief），
        合并快照是它的生命线。"""
        payload = {
            **_INIT_OK,
            "characters": [{"name": "林越", "role": "主角", "brief": "沉默寡言的少年刀客"}],
        }
        obj, errors = validate("init", payload)
        assert errors == [], f"characters 不在 schema 内，必须被忽略而非拒收：{errors}"
        merged = schemas.merge_snapshot(payload, obj)
        assert merged["characters"] == payload["characters"]

    def test_scalar_same_name_fields_are_value_identical(self):
        """「同名不同型」到不了合并步：genre 给数组会被校验拒绝（D2 行为）。

        校验通过的同名标量，规整值与原始值恒等——「规整覆盖原始」在标量上
        无可观测差异，冲突面只在嵌套容器（见退化哨兵）。"""
        payload = {**_INIT_OK, "genre": ["玄幻", "系统流"]}  # 同名不同型
        obj, errors = validate("init", payload)
        assert obj is None and errors, "同名不同型必须在校验层被拒，根本到不了合并"

        obj, errors = validate("init", _INIT_OK)
        assert errors == []
        merged = schemas.merge_snapshot(_INIT_OK, obj)
        assert merged["premise"] == _INIT_OK["premise"]
        assert merged["titles"] == _INIT_OK["titles"]

    def test_literal_formula_would_drop_nested_extra_keys(self):
        """差异实锤（防退化哨兵）：字面公式 A 会丢嵌套 extra，merge_snapshot 必须不丢。

        setup 角色 AI 自造键 "voice"（口癖），dump 整组替换后丢失——这正是
        裁决三理由 1「不丢用户看不见的东西」要防的形状。若本测试红且
        merge_snapshot 被改回字面合并，说明嵌套保真被退化。"""
        payload = {"characters": [{"name": "林越", "role": "主角", "voice": "低沉沙哑"}]}
        obj, errors = validate("setup", payload)
        assert errors == [], f"自造键不应拒收：{errors}"
        merged_a = _merge_literal(payload, obj)
        assert "voice" not in merged_a["characters"][0], "对照：字面公式确实丢嵌套 extra"
        merged = schemas.merge_snapshot(payload, obj)
        assert merged["characters"][0]["voice"] == "低沉沙哑", "产品公式必须留住它"

    def test_deep_preserves_two_level_chapter_extra(self):
        """outline 卷-章两层嵌套自造键（notes/mood）必须全保。"""
        payload = {
            "volumes": [
                {
                    "title": "第一卷",
                    "notes": "卷级自造键",
                    "chapters": [{"title": "第一章", "outline": "要点", "mood": "压抑"}],
                }
            ]
        }
        obj, errors = validate("outline", payload)
        assert errors == []
        merged = schemas.merge_snapshot(payload, obj)
        vol = merged["volumes"][0]
        assert vol["notes"] == "卷级自造键"
        assert vol["chapters"][0]["mood"] == "压抑"

    def test_merge_never_revalidates_or_mutates_inputs(self):
        """合并是纯 dict 操作：不改输入 data、不触发二次校验。"""
        import copy

        payload = dict(_SETUP_OK)
        snapshot_before = copy.deepcopy(payload)
        obj, errors = validate("setup", payload)
        assert errors == []
        merged = schemas.merge_snapshot(payload, obj)
        assert payload == snapshot_before, "合并不许修改原始输入"
        assert merged["characters"][0]["name"] == "林越"

    def test_added_none_keys_are_filtered(self):
        """公式 C：原始不存在且规整值为 None 的键不得出现（保持缺字段=无键）。

        kb-sync 打包与下游 prompt 读的是 JSON 文本，平白多出 "xxx": null 会让
        下游以为是有意义的空值——最小惊奇原则在这里有真实读者。"""
        payload = {"premise": "只有梗概"}
        obj, errors = validate("init", payload)
        assert errors == []
        merged = schemas.merge_snapshot(payload, obj)
        assert "time" not in merged, "AI 未给的 time 不得以 null 键形式新增"
        assert "characters" not in merged

    def test_existing_none_values_are_preserved(self):
        """公式 C 只滤「新增」：原始 data 里已有的 None 值键原样保留（保真优先）。"""
        payload = {"premise": "梗概", "time": None}
        obj, errors = validate("init", payload)
        assert errors == []
        merged = schemas.merge_snapshot(payload, obj)
        assert merged.get("time") is None and "time" in merged, "原始已有的 None 键不许被删"

    def test_oversized_snapshot_warns_not_blocks(self, caplog):
        """50K 软约束：序列化超长只 logger.warning 一条，不拒绝不截断。

        阈值 env（T4_SNAPSHOT_WARN_CHARS）与参数（warn_over_chars）两条路都验。"""
        import logging

        payload = {**_INIT_OK, "notes": "超" * 200}
        obj, _errors = validate("init", payload)
        schemas.merge_snapshot(payload, obj, warn_over_chars=100)
        assert any(r.levelno == logging.WARNING for r in caplog.records), "超长必须留痕"
        assert "notes" in schemas.merge_snapshot(payload, obj, warn_over_chars=10**9), "放大阈值后不告警也不阻断"

    def test_non_dict_data_warns_and_does_not_crash(self, caplog):
        """天演 MN-1：非 dict 的 data（list/None/str/42）此前静默吞成 {}——
        原始数据全丢且无痕。生产路径不可达（validate 先拒非 dict），但这是
        公开函数：现契约 = **不崩、留一条告警、规整字段照常返回**（不 raise，
        如实告警即可）。"""
        import logging

        payload = {"volumes": [{"title": "第一卷", "chapters": [{"title": "第一章", "outline": "要点"}]}]}
        obj, errors = validate("outline", payload)
        assert errors == []
        for bad in ([1, 2, 3], None, "一坨旧快照文本", 42):
            caplog.clear()
            merged = schemas.merge_snapshot(bad, obj)
            assert isinstance(merged, dict), f"data={bad!r} 时仍应返回 dict"
            assert merged.get("volumes"), f"data={bad!r} 时规整字段不得丢"
            warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
            assert any("非 dict" in w for w in warns), (
                f"data={bad!r} 必须留「非 dict」告警（不许静默吞原始数据），实得：{warns}"
            )

    def test_raw_longer_than_parsed_drops_extra_raw_elements(self):
        """天演 MN-4（**行为记录，非裁决——不改实现**）：raw 元素比 parsed 多时，
        公式 B 按 parsed 长度 enumerate，raw 多出的元素连同其自造键被丢弃。

        运行时不可达（validate 的 obj 与 data 同源等长），但 merge_snapshot 是
        公开函数——这里如实钉住当前形状，将来若有意改为"保 raw 多余元素"，
        应同步改本断言并在快照口径上给出理由。"""
        payload = {
            "volumes": [
                {"title": "第一卷", "notes": "卷一自造", "chapters": [{"title": "章A", "outline": "要点A"}]},
                {"title": "第二卷", "notes": "卷二自造", "chapters": [{"title": "章B", "outline": "要点B"}]},
            ]
        }
        obj, errors = validate("outline", payload)
        assert errors == []
        raw = {
            **payload,
            "volumes": payload["volumes"]
            + [{"title": "第三卷", "notes": "raw 独有的卷", "chapters": [{"title": "章C", "outline": "要点C"}]}],
        }
        merged = schemas.merge_snapshot(raw, obj)
        assert len(merged["volumes"]) == 2, (
            f"当前行为：raw 多出的第 3 卷被丢弃（公式 B 按 parsed 长度遍历），实得 {len(merged['volumes'])} 卷"
        )
        assert merged["volumes"][0]["notes"] == "卷一自造", "前两卷的嵌套自造键仍应保真"
        assert "第三卷" not in [v.get("title") for v in merged["volumes"]], "raw 多余元素不进结果"
