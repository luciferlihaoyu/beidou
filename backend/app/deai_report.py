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

# 展示文案。口径与 deai_signals.SIGNAL_STATUS_TEXT 一致，但这里是**报告层的显示名**。
_STATUS_LABEL = {"ok": "参与", "skipped": "未启用", "failed": "失败", "uncalibrated": "未标定"}
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


def _coverage_block(fusion: dict, n_signals: int) -> str:
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


def _signals_table(result: dict) -> str:
    weights = (result.get("fusion") or {}).get("weights") or {}
    rows = [_row([f"<th>{h}</th>" for h in ("信号", "状态", "值", "权重", "说明")])]
    for key in ("rule", "perplexity", "reviewer"):
        env = result.get(key) or {}
        rows.append(
            _row(
                [
                    f"<td>{_e(_PATH_NAME.get(key, key))}</td>",
                    f"<td>{_status_chip(str(env.get('status', '')))}</td>",
                    f'<td class="num">{_num(env.get("value"))}</td>',
                    f'<td class="num">{_num(weights.get(key))}</td>',
                    f"<td>{_e(env.get('detail'))}</td>",
                ]
            )
        )
    return "".join(rows)


def _reviewer_block(result: dict) -> str:
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
    value = env.get("value") or {}

    def _items(lst):
        if not lst:
            return '<li class="muted">（无）</li>'
        out = []
        for it in lst:
            it = it or {}
            label = it.get("label") or it.get("feature") or it.get("name") or ""
            flag = (
                ""
                if it.get("quote_verified", True)
                else '　<span class="st-failed">引文未逐字命中原文</span>'
            )
            out.append(f"<li>{_e(label)}｜引文 {_e(it.get('quote'))}{flag}</li>")
        return "".join(out)

    return (
        "<h3>AI 特征</h3><ul>"
        + _items(value.get("ai_features") or [])
        + "</ul><h3>人味证据</h3><ul>"
        + _items(value.get("human_evidence") or [])
        + "</ul>"
    )


def _style_table(style: dict) -> str:
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
    fusion = result.get("fusion") or {}
    n_signals = 3

    title = _e(meta.get("book_title")) or "（未命名作品）"
    where = f"项目 {_num(meta.get('project_id'))}｜第 {_num(meta.get('chapter_no'))} 章"
    stamp = _e(meta.get("generated_at")) if meta.get("generated_at") else ""
    caveat = _e(fusion.get("caveat")) or "实验风险分不是 AI 生成概率"

    sections = [
        f'<h1>{title}</h1><p class="muted">{where}{("｜" + stamp) if stamp else ""}</p>',
        f'<div class="caveat">{caveat}</div>',
        f'<h2>覆盖率证据</h2>{_coverage_block(fusion, n_signals)}',
        f'<h2>三路信号表</h2><table>{_signals_table(result)}</table>',
        f'<h2>审校师证据</h2>{_reviewer_block(result)}',
        f'<h2>文体指标对照</h2><table>{_style_table(result.get("style") or {})}</table>',
        f'<h2>规则命中（按严重度分组）</h2>{_rules_block(meta.get("issues"))}',
        f'<h2>检测原文</h2>{_text_block(meta.get("text"))}',
    ]
    body = "\n".join(sections)
    return (
        '<!doctype html>\n<html lang="zh-CN">\n<head><meta charset="utf-8">'
        f"<title>{title}｜AI 味检测报告</title><style>{_STYLE}</style></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )
