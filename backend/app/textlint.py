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

from . import censor

# ---- 兼容保留：旧的薄词表（新检测走 app/censor.py，此表仅备外部引用）----
# 说明：平台确切词库是内部机密且动态更新，词表越写越像「保证过审」的假承诺；
# 真正的检测逻辑已升级为 censor.check() 的分级规则 + 模式识别。
SENSITIVE_WORDS: dict[str, list[str]] = {
    "政治": ["领导人", "执政党", "颠覆国家"],
    "暴恐": ["恐怖袭击", "炸弹制作", "人体炸弹"],
    "色情": ["强奸", "乱伦", "幼女"],
    "违禁品": ["冰毒", "海洛因", "可卡因", "制毒"],
    "赌博": ["赌场", "百家乐", "赌球"],
}

# ---- 平台特色加严词库（在通用词表上叠加；按公开机审口径整理，可用自定义词补充）----
PLATFORM_PROFILES: dict[str, dict] = {
    "": {"name": "通用", "extra": {}},
    "qidian": {
        "name": "起点中文网",
        "extra": {
            # 起点对涉政/影射现实最严（历史、官场、现实题材尤其注意）
            "政治": ["国家领导人", "反动", "颠覆政权", "政治运动", "政变", "独裁"],
            "影射现实": ["真实地名暴动", "现实机构黑幕", "现实政策影射"],
            "色情": ["床戏", "肉体交易", "情色"],
        },
    },
    "fanqie": {
        "name": "番茄小说",
        "extra": {
            # 番茄（免费向）尺度最严：未成年、擦边、引流、血腥细节都是高发驳回点
            "未成年不当": ["校园霸凌细节", "未成年恋爱描写"],
            "色情": ["床戏", "肉体交易", "擦边", "挑逗"],
            "暴恐": ["砍杀", "分尸", "碎尸", "虐杀", "血腥细节"],
            "涉黑": ["黑帮火并", "收保护费", "地下赌场", "高利贷"],
            "赌博": ["网络赌博", "赌局流程"],
            "引流": ["加更群", "粉丝群", "书友群"],
            "封建迷信": ["驱邪治病", "符水"],
        },
        "escalate": ["暴力细节", "擦边描写", "涉黑违法"],
    },
    "qimao": {
        "name": "七猫小说",
        "extra": {
            "暴恐": ["砍杀", "分尸", "血腥细节"],
            "涉黑": ["黑帮", "收保护费", "高利贷"],
            "赌博": ["地下赌场", "网络赌博"],
            "色情": ["床戏", "肉体交易"],
        },
        "escalate": ["暴力细节", "擦边描写"],
    },
    "jjwxc": {
        "name": "晋江文学城",
        "extra": {
            # 晋江对情欲描写与耽美分区最敏感，对政治影射同样严格
            "色情": ["床戏", "肉体", "情欲", "欢爱", "露骨", "情色"],
            "耽美敏感": ["男男性行为", "女女性行为", "同性肉体描写"],
            "政治": ["影射时政", "现实政治"],
            "未成年不当": ["未成年性相关"],
        },
        # 晋江对情欲与擦边最敏感
        "escalate": ["擦边描写"],
    },
    "feilu": {
        "name": "飞卢小说",
        "extra": {
            "政治": ["国家领导人", "颠覆政权", "现实政治"],
            "暴恐": ["血腥细节"],
            "涉黑": ["黑帮火并"],
            "引流": ["书友群", "粉丝群"],
        },
        "escalate": ["暴力细节"],
    },
    "qunxiang": {
        "name": "QQ阅读/阅文系",
        "extra": {
            "政治": ["国家领导人", "颠覆政权"],
            "色情": ["床戏", "肉体交易"],
            "引流": ["书友群", "加更群"],
        },
    },
}

PLATFORM_CHOICES = [(k, v["name"]) for k, v in PLATFORM_PROFILES.items()]


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


def lint(text: str, platform: str = "", custom_words: list[str] | None = None) -> dict:
    """对纯文本跑全部规范检测，返回 {score, issues}（100 分制）。

    platform：目标平台 key（qidian/fanqie/qimao/jjwxc/feilu），叠加平台特色词库。
    custom_words：项目自定义敏感词（编辑点名禁用的词）。
    """
    issues: list[dict] = []

    # 1. 相邻 2 字词重复
    for m in _RE_REPEAT_2.finditer(text):
        word = m.group(1)
        if word in _LEGIT_REDUP:
            continue
        ctx = text[max(0, m.start() - 8) : m.end() + 8]
        issues.append({"type": "重复词", "severity": "medium", "detail": f"「{word}{word}」相邻重复（…{ctx}…）"})

    # 2. 平台合规检测（分级 + 合规改写建议）
    #    旧实现只有 14 个词、全部一律 high、不区分语境；现改为按公开审核口径
    #    分 block/fix/review 三档，并附「为什么踩雷 + 合规改法」。
    profile = PLATFORM_PROFILES.get(platform)
    extra = profile["extra"] if profile else None
    for issue in censor.check(
        text,
        platform=platform,
        custom_words=custom_words,
        extra_rules=extra,
        escalate=(profile or {}).get("escalate"),
    ):
        if profile and issue["category"] in (extra or {}):
            issue["detail"] = f"[{profile['name']}]" + issue["detail"]
        issues.append(issue)

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
    summary = {
        "block": sum(1 for i in issues if i.get("level") == "block"),
        "fix": sum(1 for i in issues if i.get("level") == "fix"),
        "review": sum(1 for i in issues if i.get("level") == "review"),
    }
    return {
        "score": max(0, 100 - penalty),
        "issues": issues[:50],
        "summary": summary,
        # 明确边界：不存在「敏感词清零即合规」，本检测不保证平台过审
        "note": censor.SUMMARY,
    }
