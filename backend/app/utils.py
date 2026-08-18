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
