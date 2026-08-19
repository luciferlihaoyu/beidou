import re

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</(p|h1|h2|h3|h4|li|blockquote|div)>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def strip_html(html: str) -> str:
    """将编辑器 HTML 转为纯文本，保留段落换行。"""
    text = _BLOCK_RE.sub("\n", html)
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = ((1000, "千"), (100, "百"), (10, "十"))


def cn_number(n: int) -> str:
    """阿拉伯数字转中文数字（1→一，12→十二，108→一百零八），用于章节/卷编号。"""
    if n <= 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n]
    parts: list[str] = []
    remainder = n
    for value, unit in _CN_UNITS:
        digit, remainder = divmod(remainder, value)
        if digit:
            # 10-19 读作"十X"而非"一十X"
            if not (value == 10 and digit == 1 and not parts):
                parts.append(_CN_DIGITS[digit])
            parts.append(unit)
        elif parts and remainder and parts[-1] != "零":
            parts.append("零")
    if remainder:
        parts.append(_CN_DIGITS[remainder])
    return "".join(parts).strip("零") or "零"


def order_chapters(chapters, volumes) -> list:
    """把章节按显示顺序排好：卷按 sort_order 排，卷内章节按 sort_order 排，未分卷章节在最后。

    返回排好序的章节列表；序号（第几章）就是列表中的位置（从 1 开始），跨卷连续编号。
    """
    volume_order = {v.id: (v.sort_order, v.id) for v in volumes}
    default_key = (1 << 30, 0)  # 未分卷排在最后

    def key(c):
        return (volume_order.get(c.volume_id, default_key), c.sort_order, c.id)

    return sorted(chapters, key=key)


def chapter_display_title(title: str, number: int) -> str:
    """章节显示名：序号由系统维护（第X章），title 是用户自定义部分。"""
    prefix = f"第{cn_number(number)}章"
    return f"{prefix} {title.strip()}" if title.strip() else prefix


def strip_chapter_prefix(title: str) -> str:
    """去掉旧版章节名里的"第X章"前缀（迁移用）。"""
    return re.sub(r"^第\s*[0-9零一二三四五六七八九十百千]+\s*章[\s:：、.．-]*", "", title).strip()
