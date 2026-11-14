"""t4-schema 产物 schema 契约校验层（纯校验，无 HTTP）。

铁的分层（任务书 D1）：本模块不 import FastAPI、不 raise HTTPException、
不 import app.models——对外只暴露 ``validate(node, data) -> (obj|None, errors)``，
由 router 决定怎么把 errors 变成 502。校验层掺进 HTTP 就没法被导出/复盘/单测复用。

============================================================================
真实消费字段表（D2 证据——字段以代码实际读取键为准，一个不猜）
============================================================================
逐键 grep 自 ``app/routers/ai_factory.py``（t4 动工时 3076 行版本；
每处 ``_parse_json`` 之后到落库为止的全部读取点。行号会漂移，此表为快照；
tests/test_schemas.py 顶部有同款表，两端互为锚）：

init（BookSpecOut，L1007 起 ``spec``）：
- 根须为 dict   L1010 isinstance(spec, dict)
- premise      L1010 get truthy 硬校验（旧代码唯一硬闸）；L1014-1016 kept
               覆盖写；L1024 _default_kb_query（L901-904 须 str 非空）
- titles       L1018-1019 isinstance list 后重排截 3；_default_kb_query
               L894-898（titles[0] 须 str 非空）
- genre        L1021-1022 truthy → str()[:50] 入 p.genre；kb_query L899-900
- theme        _default_kb_query L901-904（须 str 非空）
- time/place/tone/pov  L1014-1016 仅 kept 覆盖写（无读取点）；prompt 契约
               L992-999；经 L1020 book_spec_json 整体快照透传
- init prompt 另要求 characters（L999），代码零读取 → 不进 schema（见文末）

setup（SettingOut，L1127 起 ``data``）：
- characters               L1131 get or []；L1133-1134 空 → 502「AI 未生成角色」
                           ；L1148 [:12]
- characters[].name        L1152 str(c.get("name",""))[:100]
- characters[].role        L1153 str(c.get("role","配角"))[:50]
- characters[].description L1154 str(c.get("description",""))[:2000]
- characters[].arc         L1155 truthy 时拼接
- worldview                L1132 get or []（无空校验，空=合法）；L1158 [:20]
- worldview[].category     L1162 str(w.get("category","其他"))[:50]
- worldview[].title        L1163 str(w.get("title",""))[:200]
- worldview[].content      L1164 str(w.get("content",""))[:5000]

outline（OutlineOut，L1230 起 ``data``）：
- volumes                  L1233 get or []；L1234-1235 空 → 502「AI 未生成大纲」
                           ；L1256 [:20]
- volumes[].title          L1257 str(v.get("title", f"第{vi+1}卷"))[:200]
- volumes[].chapters       L1260 (v.get("chapters") or [])[:100]
                           （主体路径不查全局章数——0 章大纲旧代码静默落库；
                            D2 钦定本 schema 补"至少 1 章"闸）
- volumes[].chapters[].title    L1264 str(ch.get("title",""))[:200]
- volumes[].chapters[].outline  L1276 str(ch.get("outline",""))[:2000]
- data 整体 L1281 json.dumps 进 p.outline_json 快照；summary 无读取点，
  经快照透传（故定义为可选保留字段）

outline_continue（OutlineOut，L1388/L1393 起 ``data``）：
- volumes                  L1396 (data or {}).get("volumes") or []，isinstance
                           dict 过滤 [:10]；L1398-1399 空/planned=0 →
                           502「AI 未产出任何续写章节」
- volumes[].chapters       L1397 planned 求和（无类型过滤）；L1405 isinstance
                           dict 过滤 [:40]；L1406-1407 空卷跳过
- volumes[].title          L1410 str(v.get("title", f"第{base_vol_sort+vi+1}卷"))[:200]
- volumes[].chapters[].title    L1419 [:200]
- volumes[].chapters[].outline  L1431 [:2000]
- L1435-1437 added==0 → 502「AI 返回的大纲没有可用章节」
  （planned>0 但章元素全非 dict 的第二道闸；schema 的元素级模型类型检查
   天然覆盖此语义）

任务书 D2 猜测字段 protagonist/world/selling_points/number/synced 在代码中
**完全不存在**——本文件一个都没定义。

============================================================================
有意的取舍（均记录在交付回报，勿"顺手修复"）：
- extra="ignore"：模型自造字段不拒收；规整对象 dump 不含自造字段，
  接线时 outline_json 快照若直接 dump 规整对象会丢自造字段——接线阶段须
  自行决定快照策略（继续 dump 原始 data 或 {**data, **obj.model_dump()}）。
- 三点比旧代码更严（D2 钦定）：①OutlineOut 全局至少 1 章（补 0 章大纲
  静默落库的洞）；②每角色必须有非空 name；③数组元素级类型检查
  （旧代码对非 dict 元素直接 AttributeError 500；数字 title 落库成 "1"）。
- 截断（[:5000]/[:200]）是持久层策略，留在路由（D4），这里不设长度上限。
"""

import json
import logging
import os

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)

#: D3：单次最多列出的错误条数（超出截断并提示省略数）
MAX_ERRORS = 8

# 字段中文名与用途（missing 文案用）。只收真实消费表里的键。
_FIELD_DESC: dict[str, str] = {
    "premise": "核心梗概（这是整本书的梗概，不能空）",
    "titles": "书名候选列表",
    "genre": "类型（如 玄幻/都市/科幻）",
    "time": "时代背景",
    "place": "主要地点/世界",
    "theme": "核心主题",
    "tone": "基调",
    "pov": "叙事视角",
    "characters": "角色列表",
    "worldview": "世界观条目列表",
    "name": "角色名",
    "role": "角色定位",
    "description": "角色描述",
    "arc": "角色弧光",
    "category": "条目分类",
    "title": "标题",
    "content": "条目内容",
    "volumes": "卷列表",
    "chapters": "章节列表",
    "summary": "本卷主线",
    "outline": "剧情要点",
}

# 容器（数组）的人类量词："characters[2].name" → "第 3 个角色"
_CONTAINER_DESC: dict[str, str] = {
    "characters": "角色",
    "chapters": "章节",
    "volumes": "卷",
}

# 空串（string_too_short）的专用提示；未收录字段走通用语
_EMPTY_HINT: dict[str, str] = {
    "premise": "核心梗概必须有实际内容",
    "name": "角色名必须有实际内容",
}


class BookSpecOut(BaseModel):
    """init 产物：book_spec 八字段 + 书名候选（消费表见模块 docstring）。

    只有 premise 是旧代码的硬闸（L1010 truthy），其余字段旧代码"有则用、
    无则跳"——全部可选，与旧语义对齐。
    """

    model_config = ConfigDict(extra="ignore")

    premise: str = Field(min_length=1)  # L1010 truthy 硬闸；空串等价于缺
    titles: list[str] | None = None  # L1018-1019
    genre: str | None = None  # L1021-1022 / kb_query L899-900
    time: str | None = None  # 快照透传（无读取点）
    place: str | None = None  # 快照透传
    theme: str | None = None  # kb_query L901-904
    tone: str | None = None  # 快照透传
    pov: str | None = None  # 快照透传


class CharacterOut(BaseModel):
    """setup 角色卡条目。name 必填非空（任务书 §3.1：每个有 name）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)  # L1152
    role: str | None = None  # L1153（旧默认"配角"，缺省语义由路由保留）
    description: str | None = None  # L1154
    arc: str | None = None  # L1155


class WorldviewOut(BaseModel):
    """setup 世界观条目。全字段可选——旧代码每项都有默认值（L1162-1164）。"""

    model_config = ConfigDict(extra="ignore")

    category: str | None = None  # L1162（旧默认"其他"）
    title: str | None = None  # L1163
    content: str | None = None  # L1164


class SettingOut(BaseModel):
    """setup 产物：角色卡 + 世界观。

    characters 空壳闸 = 旧 L1133-1134「AI 未生成角色」502 语义；
    worldview 无空校验（旧 L1132 无闸），且 None 必须放行（旧 ``or []`` 口径）。
    """

    model_config = ConfigDict(extra="ignore")

    characters: list[CharacterOut] = Field(min_length=1)  # L1131-1134
    worldview: list[WorldviewOut] | None = None  # L1132 / L1158


class ChapterOut(BaseModel):
    """章条目。title/outline 旧代码均可缺省（L1264/L1276 get 带默认）。"""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None  # L1264/L1419
    outline: str | None = None  # L1276/L1431


class VolumeOut(BaseModel):
    """卷条目。title 旧默认"第 N 卷"由路由的 f-string 生成，缺省语义保留。

    summary 无读取点、纯快照透传，定义为可选以保快照保真。
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None  # L1257/L1410
    summary: str | None = None  # 快照透传（无读取点）
    chapters: list[ChapterOut] = []  # L1260/L1397/L1405


class OutlineOut(BaseModel):
    """outline / outline_continue 共用产物：卷-章骨架。

    volumes 空壳闸 = 旧「AI 未生成大纲」L1234-1235 语义；
    全局至少 1 章 = 旧「AI 未产出任何续写章节」L1398-1399 +「没有可用章节」
    L1435-1437 的合并语义（D2 钦定，主路径 outline 也生效——补 0 章静默落库的洞）。
    """

    model_config = ConfigDict(extra="ignore")

    volumes: list[VolumeOut] = Field(min_length=1)  # L1233-1235 / L1396-1399

    @model_validator(mode="after")
    def _at_least_one_chapter(self) -> "OutlineOut":
        if sum(len(v.chapters) for v in self.volumes) < 1:
            raise ValueError("volumes：至少要包含 1 个章节（全书合计章数不能为 0）")
        return self


#: 任务书 §3.1 钦定的节点 → 模型映射（D2：续写复用 Outline）
NODE_MODELS: dict[str, type[BaseModel]] = {
    "init": BookSpecOut,
    "setup": SettingOut,
    "outline": OutlineOut,
    "outline_continue": OutlineOut,
}


def _render_path(loc: tuple) -> str:
    """pydantic loc → D3 下标路径形式：("characters", 2, "name") → characters[2].name。"""
    if not loc:
        return "输入整体"
    parts: list[str] = []
    for seg in loc:
        if isinstance(seg, int):
            parts.append(f"[{seg}]")
        else:
            parts.append(("." if parts else "") + str(seg))
    return "".join(parts)


def _missing_text(loc: tuple) -> str:
    """missing 错误文案：带容器序号（第 3 个角色缺少角色名）或字段用途说明。"""
    path = _render_path(loc)
    leaf = loc[-1] if loc else ""
    desc = _FIELD_DESC.get(str(leaf), "必填字段")
    # 找最后一个 int 段：它前面的段名是容器，int 值 +1 是人类序号
    idx = next((i for i in range(len(loc) - 1, -1, -1) if isinstance(loc[i], int)), None)
    if idx is not None and idx > 0:
        container = str(loc[idx - 1])
        cdesc = _CONTAINER_DESC.get(container, container)
        return f"{path}：第 {loc[idx] + 1} 个{cdesc}缺少{desc}"
    return f"{path}：缺少{desc}"


def _humanize(exc) -> list[str]:
    """pydantic ValidationError → D3 中文文案列表（哪个字段 + 要什么 + 实际是什么）。

    参数是 ValidationError（或有 ``.errors()`` 方法的鸭子类型替身，便于单测
    伪造未知错误类型）。未知 error type（pydantic 版本升级后新类型）不抛、
    不漏英文原句，输出通用中文——兜底优先于精确。
    """
    out: list[str] = []
    for err in exc.errors():
        etype = str(err.get("type", ""))
        loc = tuple(err.get("loc") or ())
        path = _render_path(loc)
        actual = type(err.get("input")).__name__
        if etype == "missing":
            out.append(_missing_text(loc))
        elif etype == "string_type":
            out.append(f"{path}：应为字符串，实际是 {actual}")
        elif etype == "list_type":
            out.append(f"{path}：应为数组，实际是 {actual}")
        elif etype in ("model_type", "object_type", "dict_type"):
            out.append(f"{path}：应为 JSON 对象（大括号包裹），实际是 {actual}")
        elif etype == "string_too_short":
            hint = _EMPTY_HINT.get(str(loc[-1]) if loc else "", "该字段必须有实际内容")
            out.append(f"{path}：不能为空串（{hint}）")
        elif etype == "too_short":
            min_len = (err.get("ctx") or {}).get("min_length", 1)
            out.append(f"{path}：不能为空数组（至少需要 {min_len} 项）")
        elif etype == "value_error":
            # 自定义 validator 抛的 ValueError，消息本身已是自含路径的中文
            err_text = str((err.get("ctx") or {}).get("error", ""))
            out.append(err_text or f"{path}：取值不符合契约要求")
        else:
            out.append(f"{path}：字段格式不符合契约要求，请检查它的类型与结构")
    if len(out) > MAX_ERRORS:
        omitted = len(out) - MAX_ERRORS
        out = out[:MAX_ERRORS] + [f"…（其余 {omitted} 条同类问题已省略）"]
    return out


def validate(node: str, data: object) -> tuple[object | None, list[str]]:
    """校验某节点的 AI 产物。返回 (规整后的对象或 None, 中文错误列表)。

    - 通过：errors == []，obj 为对应模型的规整实例；
    - 不通过：obj 为 None，errors 为 D3 格式中文列表（至多 MAX_ERRORS 条）；
    - 未知节点：防御性拒绝（任务书 §3.2：review/synopsis 等不接）。
    """
    model = NODE_MODELS.get(node)
    if model is None:
        known = "、".join(NODE_MODELS)
        return None, [f"未知节点 {node}：不在 t4 契约范围（{known}）"]
    try:
        obj = model.model_validate(data)
    except ValidationError as exc:
        return None, _humanize(exc)
    return obj, []


def _merge_nested_elem(raw: object, parsed: object) -> object:
    """公式 B 元素级递归：规整键覆盖、原始额外键保留、嵌套数组继续下钻。

    model_dump() 重建嵌套模型时会丢元素级 extra 键（角色自造键 voice、
    卷级 notes、章级 mood），本递归把它们找回来——校验层放宽、存储层
    必须同样放宽（裁决三理由 1）。"""
    if isinstance(raw, dict) and isinstance(parsed, dict):
        merged: dict = {**raw, **parsed}
        for key, val in parsed.items():
            r = raw.get(key)
            if isinstance(val, list) and isinstance(r, list) and val and all(isinstance(e, dict) for e in val):
                merged[key] = [
                    _merge_nested_elem(r[i], e) if i < len(r) and isinstance(r[i], dict) else e
                    for i, e in enumerate(val)
                ]
        return merged
    return parsed


def merge_snapshot(data: dict, obj: BaseModel, *, warn_over_chars: int | None = None) -> dict:
    """落库快照合并（碧霄拍板：公式 B 嵌套保真 + C 滤新增 None）。

    - 基底 ``{**原始 data, **规整后字段}``：原始字段保真（init 的 characters、
      各处自造字段），规整字段覆盖同名标量；
    - 公式 B：模型容器字段（characters/volumes 等数组）逐元素
      ``{**原始元素, **规整元素}`` 递归——嵌套 extra 键一个不丢；
    - 公式 C：过滤「原始不存在且规整值为 None」的键——保持「缺字段=无键」
      的现状形状（kb-sync 打包与下游 prompt 读 JSON 文本，平白多出 null 键
      会被当成有意义的空值）；
    - 50K 软约束：序列化长度超阈值只 logger.warning 一条，不拒绝不截断
      （阈值 env ``T4_SNAPSHOT_WARN_CHARS`` 可覆盖，默认 50000；参数优先）。

    纯 dict 操作：不改输入、不触发二次校验。
    """
    if not isinstance(data, dict):
        # 天演 MN-1：此前静默吞成 {}，原始数据全丢且无痕。生产路径不可达
        # （validate 先拒非 dict），公开函数如实告警即可——不 raise（raise
        # 只是增加炸的面积），然后按空基底继续合并。
        logger.warning(
            "快照合并收到非 dict（%s），原始数据未并入——仅保留规整字段",
            type(data).__name__,
        )
        data = {}
    dump = obj.model_dump()
    merged = {**data, **dump}
    for key, val in dump.items():  # 公式 B
        raw = data.get(key)
        if isinstance(val, list) and isinstance(raw, list) and val and all(isinstance(e, dict) for e in val):
            merged[key] = [
                _merge_nested_elem(raw[i], e) if i < len(raw) and isinstance(raw[i], dict) else e
                for i, e in enumerate(val)
            ]
    for key in [k for k in merged if k not in data and merged[k] is None]:  # 公式 C
        del merged[key]
    try:
        threshold = (
            warn_over_chars if warn_over_chars is not None else int(os.environ.get("T4_SNAPSHOT_WARN_CHARS", "50000"))
        )
    except ValueError:
        threshold = 50000
    text = json.dumps(merged, ensure_ascii=False)
    if len(text) > threshold:
        logger.warning(
            "t4 快照合并结果超长（%d 字符 > 阈值 %d）：疑似含超大自造字段，仅告警不阻断",
            len(text),
            threshold,
        )
    return merged
