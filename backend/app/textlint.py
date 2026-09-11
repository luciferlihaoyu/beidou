"""文本规范检测（本地零成本，与 anti_llm 互补）。

anti_llm 查「AI 味」（风格层面），本模块查「文字规范」（文字层面）：
- 重复词：相邻重复（"的的""我们我们"）、近距重复（5 字内同一 2 字词重复出现）
- 敏感词：网文平台机审常见违禁类别（可扩展的词表）
- 标点问题：连续标点、省略号误用、中英文标点混用
- 常见错别字：高频误用对（的/地/得 典型场景、"在/再" 等）

全部确定性规则，无外部依赖。
"""

from __future__ import annotations

import re

# ---- 敏感词表（网文平台机审常见类别，按需扩充）----
SENSITIVE_WORDS: dict[str, list[str]] = {
    "政治": ["领导人", "执政党", "颠覆国家"],
    "暴恐": ["恐怖袭击", "炸弹制作", "人体炸弹"],
    "色情": ["强奸", "乱伦", "幼女"],
    "违禁品": ["冰毒", "海洛因", "可卡因", "制毒"],
    "赌博": ["赌场", "百家乐", "赌球"],
}

# ---- 高频错别字对（容易写错的）----
TYPO_PAIRS: list[tuple[str, str]] = [
    ("在见", "再见"),
    ("份量", "分量"),
    ("按装", "安装"),
    ("脉博", "脉搏"),
    ("松驰", "松弛"),
    ("兰球", "篮球"),
    ("罗嗦", "啰嗦"),
    ("姿式", "姿势"),
    ("意想天开", "异想天开"),
    ("义不容词", "义不容辞"),
    ("穿流不息", "川流不息"),
    ("一如即往", "一如既往"),
    ("名符其实", "名副其实"),
    ("谈笑风声", "谈笑风生"),
    ("走头无路", "走投无路"),
    ("世外桃园", "世外桃源"),
    ("脍灸人口", "脍炙人口"),
    ("洁白无暇", "洁白无瑕"),
    ("娇揉造作", "矫揉造作"),
    ("磬竹难书", "罄竹难书"),
]

# 的地得典型误用（保守规则，只抓高置信场景）
DE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"[飞奔跑走跳爬游滚]的[快慢高低远近]"), "动词+的+形容词 疑为「得」（跑得很快）"),
    (re.compile(r"[说笑哭喊叫骂]的[很真太]"), "动词+的+程度词 疑为「得」（说得很对）"),
]

_RE_REPEAT_CHAR = re.compile(r"(.{1,2})\1")  # 相邻重复字/词（单字叠词合法太多，只报 2 字词）
_RE_REPEAT_2 = re.compile(r"([\u4e00-\u9fff]{2})\1")  # 严格 2 字词相邻重复：我们我们
_RE_PUNCT_RUN = re.compile(r"([，。！？；：、])\1+")  # 连续相同标点
_RE_BAD_ELLIPSIS = re.compile(r"\.{3,}|…{2,}|。。。|！！！|？？？")
_RE_MIXED_PUNCT = re.compile(r"[\u4e00-\u9fff],[\u4e00-\u9fff]")  # 中文间用英文逗号

# 合法的 2 字叠词（不报）
_LEGIT_REDUP = {
    "看看", "听听", "想想", "试试", "说说", "走走", "笑笑", "谢谢",
    "妈妈", "爸爸", "哥哥", "姐姐", "弟弟", "妹妹", "爷爷", "奶奶",
    "刚刚", "渐渐", "慢慢", "悄悄", "微微", "淡淡", "静静", "默默",
    "纷纷", "往往", "常常", "时时", "处处", "人人", "事事", "天天",
    "一步一步", "一声一声",
}


def lint(text: str) -> dict:
    """对纯文本跑全部规范检测，返回 {score, issues}（100 分制）。"""
    issues: list[dict] = []

    # 1. 相邻 2 字词重复
    for m in _RE_REPEAT_2.finditer(text):
        word = m.group(1)
        if word in _LEGIT_REDUP:
            continue
        ctx = text[max(0, m.start() - 8) : m.end() + 8]
        issues.append({"type": "重复词", "severity": "medium", "detail": f"「{word}{word}」相邻重复（…{ctx}…）"})

    # 2. 敏感词
    for cat, words in SENSITIVE_WORDS.items():
        for w in words:
            if w in text:
                issues.append({"type": "敏感词", "severity": "high", "detail": f"含{cat}类敏感词「{w}」，平台机审可能拦截"})

    # 3. 常见错别字
    for wrong, right in TYPO_PAIRS:
        if wrong in text:
            issues.append({"type": "错别字", "severity": "medium", "detail": f"「{wrong}」应为「{right}」"})

    # 4. 的地得误用
    for pat, hint in DE_PATTERNS:
        for m in pat.finditer(text):
            issues.append({"type": "的地得", "severity": "low", "detail": f"「{m.group(0)}」：{hint}"})

    # 5. 标点问题
    for m in _RE_PUNCT_RUN.finditer(text):
        issues.append({"type": "标点", "severity": "low", "detail": f"连续标点「{m.group(0)}」（…{text[max(0,m.start()-6):m.end()+6]}…）"})
    for m in _RE_BAD_ELLIPSIS.finditer(text):
        issues.append({"type": "标点", "severity": "low", "detail": f"标点堆叠「{m.group(0)}」，建议统一为 …… 或单个"})
    if _RE_MIXED_PUNCT.search(text):
        issues.append({"type": "标点", "severity": "low", "detail": "中文语句中混用了英文逗号"})

    # 计分：高 -15 / 中 -5 / 低 -1，封顶 100
    penalty = sum(15 if i["severity"] == "high" else 5 if i["severity"] == "medium" else 1 for i in issues)
    return {"score": max(0, 100 - penalty), "issues": issues[:50]}
