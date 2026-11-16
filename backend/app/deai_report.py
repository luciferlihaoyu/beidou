"""t8-deai-report：自包含单文件 HTML 检测报告。

双击可开、Ctrl+P 可存 PDF：**内联样式、零外链、零脚本**。

三条实现约束（都是判据逼出来的，不是偏好）：

1. **不取系统时钟**。时间戳只能由调用方经 `meta["generated_at"]` 传入。
   否则同一章两次报告不可对比，且 `test_same_input_same_bytes` 会红。
2. **检测原文是不可信输入**。它来自模型产出与人工改动，必须逐字转义后才能显示；
   转义不等于丢弃——被检文本仍要看得见（`test_malicious_text_is_escaped_into_visible_text`）。
3. **不显示没有的东西**。t7 的 `collect_signals` 顶层只有
   rule / perplexity / reviewer / style / fusion，**没有 coverage、也不含原文**；
   所以覆盖率从 `fusion.participating` 推导，原文与规则命中由 `meta` 传入，
   没传就如实写「未随结果传入」，**不许留白**（留白会被读成"没问题"）。
"""

from __future__ import annotations

import html as _html

from .deai_signals import _PATH_ORDER  # 表行与覆盖率分母的唯一来源（评审 C3）

# 展示文案。口径与 deai_signals.SIGNAL_STATUS_TEXT 一致，但这里是**报告层的显示名**。
_STATUS_LABEL = {
    "ok": "参与",
    "skipped": "未启用",
    "failed": "失败",
    "uncalibrated": "未标定",
    # 评审 B-2：太短哨兵那一路**没有真正检测过**，必须在报告里有自己的说法，
    # 不许混进"未启用"（未启用是配置问题，哨兵是数据不够）。
    "degenerate": "哨兵值·未真正检测",
}
_SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}
_PATH_NAME = {"rule": "规则", "perplexity": "困惑度", "reviewer": "审校师"}

_STYLE = (
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:24px;"
    "line-height:1.6;color:#1c1c1e;background:#fafafa}"
    "h2{font-size:1.05rem;margin:28px 0 8px;padding-bottom:4px;border-bottom:2px solid #d0d0d5}"
    ".caveat{background:#fff4e5;border-left:4px solid #e08a2e;padding:10px 14px;font-weight:600}"
    "table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}"
    "th,td{border:1px solid #d8d8de;padding:6px 8px;text-align:left;vertical-align:top}"
    "th{background:#efeff3}td.num{text-align:right;font-variant-numeric:tabular-nums}"
    ".st-ok{color:#1a7f37}.st-skipped{color:#8a6d1f}.st-failed{color:#b3261e}"
    ".st-uncalibrated{color:#6b5b95}"
    "pre{white-space:pre-wrap;word-wrap:break-word;background:#fff;border:1px solid #d8d8de;"
    "padding:12px;border-radius:4px}"
    ".muted{color:#6e6e73;font-size:.86rem}"
)


def _e(value) -> str:
    """转义一切插入文本（None → 空串）。quote=True 让引号也转义，防属性逃逸。"""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def _num(value) -> str:
    return "—" if value is None else _e(value)


def _status_chip(status: str) -> str:
    # 未知状态必须显形成「未知状态（xxx）」，不许静默按 ok 渲染
    label = _STATUS_LABEL.get(status, f"未知状态（{status}）")
    return f'<span class="st-{_e(status)}">{_e(label)}</span>'


def _row(cells: list) -> str:
    return "<tr>" + "".join(cells) + "</tr>"


def _coverage_block(fusion: dict, n_signals: int, notes: list) -> str:
    if not isinstance(fusion, dict):
        notes.append(f"融合结果形态异常（收到 {type(fusion).__name__}），无风险分")
        return '<p style="font-size:1.3rem;font-weight:700">—/—</p><p class="st-failed">融合数据形态异常，本报告不构成判断依据。</p>'
    participating = fusion.get("participating") or []
    weights = fusion.get("weights") or {}
    got = len(participating)
    head = f'<p style="font-size:1.3rem;font-weight:700">{got}/{n_signals}</p>'
    if not participating:
        return head + '<p class="muted">没有任何一路参与打分——本报告不构成判断依据。</p>'
    detail = (
        "参与路："
        + "、".join(f"{_e(_PATH_NAME.get(n, n))}（权重 {_e(weights.get(n))}）" for n in participating)
        + f"｜风险分 {_num(fusion.get('score'))}"
    )
    return head + f'<p class="muted">{detail}</p>'


def _signal_paths(result: dict) -> tuple[list, list]:
    """表行与覆盖率分母都从这里来——**不写死三行**。

    评审 C3：旧实现写死 `("rule","perplexity","reviewer")` 与 `n_signals = 3`，
    将来 t7 加第四路时，那一路的**数据在场而报告不显示**，覆盖率还说 1/3 ——
    那是**静默谎报**，比报错更难发现。未知路现在显式列成「未识别的信号路」。
    """
    known = list(_PATH_ORDER)
    extras = [
        k
        for k, v in result.items()
        if k not in known
        and k not in ("style", "fusion")
        and isinstance(v, dict)
        and "status" in v
    ]
    return known, extras


def _signals_table(result: dict, notes: list) -> str:
    weights = (result.get("fusion") or {}).get("weights") or {} if isinstance(result.get("fusion"), dict) else {}
    rows = [_row([f"<th>{h}</th>" for h in ("信号", "状态", "值", "权重", "说明")])]
    known, extras = _signal_paths(result)
    if extras:
        notes.append(f"结果里出现未登记在 _PATH_ORDER 的信号路：{'、'.join(extras)}（已列出，但权重与融合不参与）")
    for key in known + extras:
        env = result.get(key) or {}
        rows.append(
            _row(
                [
                    f"<td>{_e(_PATH_NAME.get(key, key))}"
                    + (f'<span class="muted">（未识别的信号路）</span>' if key in extras else "")
                    + "</td>",
                    f"<td>{_status_chip(str(env.get('status', '')))}</td>",
                    f'<td class="num">{_num(env.get("value"))}</td>',
                    f'<td class="num">{_num(weights.get(key))}</td>',
                    f"<td>{_e(env.get('detail'))}</td>",
                ]
            )
        )
    return "".join(rows)


def _reviewer_block(result: dict, notes: list) -> str:
    """审校师证据：AI 特征 / 人味证据分组，每条带引文。

    🔴 失败与未启用**不许渲染成空白**——空白会被读成"审校师没发现问题"，
    而事实是"审校师没工作"。这个形状批次一 MJ-4 栽过（失败当没问题）。
    """
    env = result.get("reviewer") or {}
    status = str(env.get("status", ""))
    if status != "ok":
        return (
            f'<p>状态：{_status_chip(status)}　{_e(env.get("detail"))}</p>'
            '<p class="muted">本路未产出证据，下方两组为空是「没有证据」而不是「没有问题」。</p>'
        )
    value = env.get("value")
    if value is not None and not isinstance(value, dict):
        notes.append(f"审校师证据形态异常（收到 {type(value).__name__}），本路结果不可用")
        return '<p class="st-failed">数据形态异常，本路结果不可用（不是「没有发现问题」）。</p>'
    value = value or {}

    def _items(lst):
        if not lst:
            return '<li class="muted">（无）</li>'
        out = []
        for it in lst:
            it = it or {}
            label = it.get("label") or it.get("feature") or it.get("name") or ""
            # 🔴 缺键的默认方向必须是**保守**（评审 C1 附带）：原先 `get(..., True)`
            # 等于"没标记就算核验过"，把旗标静默摘掉；现在没标记就是没核验。
            verified = it.get("quote_verified")
            if verified is True:
                flag = ""
            elif verified is False:
                flag = '　<span class="st-failed">引文未逐字命中原文</span>'
            else:
                flag = '　<span class="st-failed">引文未核验（结果缺少核验标记）</span>' 
            out.append(f"<li>{_e(label)}｜引文 {_e(it.get('quote'))}{flag}</li>")
        return "".join(out)

    return (
        "<h3>AI 特征</h3><ul>"
        + _items(value.get("ai_features") or [])
        + "</ul><h3>人味证据</h3><ul>"
        + _items(value.get("human_evidence") or [])
        + "</ul>"
    )


def _style_table(style, notes: list) -> str:
    if not isinstance(style, dict):
        notes.append(f"文体指标形态异常（收到 {type(style).__name__}），本节无数据")
        return _row(['<td colspan="5" class="st-failed">数据形态异常，未能解析</td>'])
    rows = [_row([f"<th>{h}</th>" for h in ("维度", "实测值", "人类区间", "偏离", "标定状态")])]
    for name, d in (style or {}).items():
        d = d or {}
        low, high = d.get("human_low"), d.get("human_high")
        rng = "—" if low is None or high is None else f"{_e(low)} ~ {_e(high)}"
        rows.append(
            _row(
                [
                    f"<td>{_e(name)}</td>",
                    f'<td class="num">{_num(d.get("value"))}</td>',
                    f'<td class="num">{rng}</td>',
                    f'<td class="num">{_num(d.get("deviation"))}</td>',
                    f"<td>{_status_chip(str(d.get('status', '')))}</td>",
                ]
            )
        )
    if len(rows) == 1:
        rows.append(_row(['<td colspan="5" class="muted">未收到文体指标数据</td>']))
    return "".join(rows)


def _rules_block(issues) -> str:
    if issues is None:
        return '<p class="muted">规则命中明细未随结果传入（只有分数时无法展示命中项）。</p>'
    if not issues:
        return "<p>规则命中：无。</p>"
    grouped: dict = {}
    for it in issues:
        it = it or {}
        grouped.setdefault(str(it.get("severity", "low")), []).append(it)
    order = ["high", "medium", "low"]
    parts = []
    for sev in order + [s for s in sorted(grouped) if s not in order]:
        items = grouped.get(sev)
        if not items:
            continue
        lis = "".join(f"<li><b>{_e(it.get('type'))}</b>｜{_e(it.get('detail'))}</li>" for it in items)
        parts.append(f'<h3>严重度：{_e(_SEVERITY_LABEL.get(sev, sev))}</h3><ul>{lis}</ul>')
    return "".join(parts)


def _text_block(text) -> str:
    if text is None:
        return '<p class="muted">检测原文未随结果传入。</p>'
    return f"<pre>{_e(text)}</pre>"


def build_report_html(result: dict, meta: dict) -> str:
    """把 t7 的三路结果渲染为**单个自包含 HTML**。

    `meta` 认识的键：project_id / book_title / chapter_no / text / issues / generated_at。
    `text`、`issues` 缺失时在报告里**如实标注**（见模块头注第 3 条）。
    """
    result = result or {}
    meta = meta or {}
    fusion = result.get("fusion")  # u0c-fix：原始值传下去，形态判断归 _coverage_block（:74）——原先先过滤成 {} 会让它的 isinstance 分支变死代码
    fusion_dict = fusion if isinstance(fusion, dict) else {}  # caveat 取值专用：非字典融合没有键可读
    notes: list[str] = []  # 形态异常/未登记路，汇总成页首警告条
    _known, _extras = _signal_paths(result)
    n_signals = len(_known) + len(_extras)

    title = _e(meta.get("book_title")) or "（未命名作品）"
    where = f"项目 {_num(meta.get('project_id'))}｜第 {_num(meta.get('chapter_no'))} 章"
    stamp = _e(meta.get("generated_at")) if meta.get("generated_at") else ""
    caveat = _e(fusion_dict.get("caveat")) or "实验风险分不是 AI 生成概率"

    # 🔴 顺序要紧：notes 是**渲染各表时**才填的，所以警告条必须先算完各段、
    # 再插到页首。原先把 warn_html 写在 sections 字面量里、排在各表之前，
    # Python 按字面量顺序求值 → 那时 notes 还是空的 → 警告条永远不出现（假修复）。
    coverage_html = _coverage_block(fusion, n_signals, notes)
    signals_html = _signals_table(result, notes)
    reviewer_html = _reviewer_block(result, notes)
    style_html = _style_table(result.get("style"), notes)
    rules_html = _rules_block(meta.get("issues"))
    text_html = _text_block(meta.get("text"))
    warn_html = (
        '<div class="caveat">' + "；".join(_e(n) for n in notes) + "</div>" if notes else ""
    )
    sections = [
        f'<h1>{title}</h1><p class="muted">{where}{("｜" + stamp) if stamp else ""}</p>',
        f'<div class="caveat">{caveat}</div>',
        warn_html,
        f'<h2>覆盖率证据</h2>{coverage_html}',
        f'<h2>信号表（共 {n_signals} 路）</h2><table>{signals_html}</table>',
        f'<h2>审校师证据</h2>{reviewer_html}',
        f'<h2>文体指标对照</h2><table>{style_html}</table>',
        f'<h2>规则命中（按严重度分组）</h2>{rules_html}',
        f'<h2>检测原文</h2>{text_html}',
    ]
    body = "\n".join(sections)
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head><meta charset="utf-8">'
        f"<title>{title}｜AI 味检测报告</title><style>{_STYLE}</style></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
