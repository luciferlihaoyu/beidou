"""章节生成失败分类：把上游/内部错误翻译成「病因 + 怎么办」。

背景：此前生成失败只写 status="failed"，原因不落库也不展示，用户只看到
一个「失败」标签，既不知道是自己配置问题还是模型问题，也不知道该改什么。
本模块是纯函数（无 IO，可单测），把原始错误文本归到有限几类，并给出
可操作的建议；原始文本仍完整保留在 job.last_error 里以便排查。
"""

from __future__ import annotations

import re

__all__ = ["classify_failure", "FailureInfo"]

# 分类码 → (中文病因, 建议动作)
KINDS: dict[str, tuple[str, str]] = {
    "auth": (
        "模型接口鉴权失败（Key 无效/过期/无权限）",
        "到「设置 → AI 配置」检查该配置的 API Key 与 Base URL；保存后重试这一章",
    ),
    "quota": (
        "模型额度不足或触发限流",
        "检查账户余额；也可以换一个模型路由（项目设置里可给正文单独指定模型），过几分钟再重试",
    ),
    "model_not_found": (
        "模型名或接口地址不对",
        "到「设置 → AI 配置」重新拉取模型列表并选择存在的模型（常见于供应商改名或下架）",
    ),
    "context_too_long": (
        "上下文超出模型长度上限",
        "把「最近章节原文/记忆卡」数量调小，或换一个上下文窗口更大的模型；也可先把前情摘要压缩一下",
    ),
    "timeout": (
        "模型响应超时",
        "换更快的模型，或稍后重试；长章节建议关闭「全自动」逐章确认",
    ),
    "network": (
        "连不上模型接口（网络/代理/Base URL 不通）",
        "检查 Base URL 是否可达，以及部署环境能否出网",
    ),
    "empty_output": (
        "模型返回空内容或内容过短",
        "重试一次；若持续出现，多半是该模型对长文写作不稳，换一个模型",
    ),
    "bad_json": (
        "模型输出不是合法 JSON（多用于设定/大纲等结构化任务）",
        "重试；若持续失败，换一个指令遵循更好的模型",
    ),
    "quality_gate": (
        "质量门禁提前收工（连续多章 AI 味过低）",
        "不是错误而是保护：说明最近几章写得太「AI 味」，建议人工介入调整设定或风格要求",
    ),
    "stopped": (
        "已手动停止",
        "这不是失败，重新生成即可",
    ),
    "unknown": (
        "未归类的失败",
        "展开「查看原因」复制原始报错，便于定位",
    ),
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # 上游返回码（_stream_openai / _chat_text 会带上 status_code 与响应体）
    ("auth", re.compile(r"\b(401|403)\b|unauthorized|invalid[_ ]api[_ ]key|forbidden|authentication", re.I)),
    ("quota", re.compile(r"\b(402|429)\b|rate[_ ]limit|quota|insufficient|balance|too many requests|exceeded", re.I)),
    ("model_not_found", re.compile(r"\b404\b|model[_ ]not[_ ]found|no such model|does not exist", re.I)),
    (
        "context_too_long",
        re.compile(
            r"context[_ ]length|too long|maximum context|max_tokens|token limit|reduce the length|上下文过长",
            re.I,
        ),
    ),
    ("timeout", re.compile(r"timeout|timed out|超时", re.I)),
    ("network", re.compile(r"connect|connection|network|proxy|ssl|无法连接|dns", re.I)),
    ("empty_output", re.compile(r"内容过短|空内容|empty", re.I)),
    ("bad_json", re.compile(r"json|解析失败|parse", re.I)),
    ("quality_gate", re.compile(r"质量门禁", re.I)),
    ("stopped", re.compile(r"已手动停止|已停止", re.I)),
]


class FailureInfo:
    """分类结果（便于测试断言，不做成 dataclass 以保持零依赖）"""

    __slots__ = ("code", "title", "hint", "raw")

    def __init__(self, code: str, title: str, hint: str, raw: str):
        self.code = code
        self.title = title
        self.hint = hint
        self.raw = raw

    def as_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "hint": self.hint, "raw": self.raw[:1000]}


def classify_failure(raw: str | None) -> FailureInfo:
    """把原始错误文本归类。未知情况返回 unknown，绝不抛异常。"""
    text = (raw or "").strip()
    code = "unknown"
    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            code = kind
            break
    title, hint = KINDS[code]
    return FailureInfo(code=code, title=title, hint=hint, raw=text)
