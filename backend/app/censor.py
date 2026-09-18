"""平台合规检测（网文向，纯本地零成本）。

边界声明（重要，写在最前面）：
    各平台的**确切屏蔽词库是内部机密且动态更新**，任何外部词表都不可能完整或
    实时。本模块的目标不是"词表清零即合规"，而是「投稿前少踩明显的雷」：
    按公开审核口径把**可确定的风险信号**分级列出，并给出**合规改写方向**。

    本模块不提供、也不应被用于「绕过审核的写法」——只提示风险与合规改法。

分级（对齐平台审核口径的三类处置）：
    block  硬红线：法律法规/暴恐极端/淫秽色情/赌博吸毒/教唆犯罪/未成年不当/
           邪教与迷信现实宣传/隐私人肉。出现即需删改，不要试图软化成同义词。
    fix    平台秩序与质量：引流联系方式、正文里的作者话、广告与交易、
           乱码与占位符、重复填充、水文特征。高概率影响审核或推荐。
    review 依赖语境：暴力/犯罪细节、现实原型、灵异涉及现实知识、擦边描写、
           拼音或拆字等不规范写法。需人工结合语境判断，不自动判定违规。

判定原则（来自平台公开规范的执行口径）：
    区分「虚构出现」与「宣扬 / 教学 / 号召」——灵异、反派犯罪、战斗冲突可以
    作为剧情元素；一旦变成现实可复现的教程、价值鼓励或模仿诱导即升级为红线。
"""

from __future__ import annotations

import re

__all__ = ["check", "LEVELS", "CATEGORY_META", "CATEGORY_ALIAS", "resolve_category", "level_of_severity", "SUMMARY"]

# ---- 级别定义 ----
LEVELS = {
    "block": "硬红线（发布前必须处理）",
    "fix": "高概率影响审核/推荐（建议改）",
    "review": "需结合语境人工判断",
}

# 级别 → 既有 severity 口径（保持旧前端与计分兼容）
_LEVEL_SEVERITY = {"block": "high", "fix": "medium", "review": "low"}
_SEVERITY_LEVEL = {"high": "block", "medium": "fix", "low": "review"}


def level_of_severity(severity: str) -> str:
    return _SEVERITY_LEVEL.get(severity, "review")


# ---- 类别元信息：为什么踩雷 + 合规改写方向 ----
CATEGORY_META: dict[str, dict[str, str]] = {
    "涉政有害": {
        "level": "block",
        "why": "危害国家统一/安全，或涉及恐怖主义、极端主义、民族仇恨",
        "how": "网文一律架空：朝代、国名、势力、人名全部虚构，不影射现实政治与真实事件",
    },
    "邪教迷信": {
        "level": "block",
        "why": "宣扬邪教、破坏宗教政策，或把迷信写成现实有效的知识",
        "how": "超自然力量牢牢锁在架空设定内；不写现实可复现的仪式、符法、治病方法，不与现实宗教挂钩",
    },
    "淫秽色情": {
        "level": "block",
        "why": "淫秽色情与露骨性描写（涉未成年为绝对红线）",
        "how": "情欲场景点到为止：转场留白、写情绪与关系变化，不写身体细节与性行为过程",
    },
    "未成年不当": {
        "level": "block",
        "why": "未成年人的性化、虐待、霸凌猎奇属平台零容忍",
        "how": "涉及未成年角色时一律设定成年；风险情节突出制止、保护、求助与后果，不写细节、不作卖点",
    },
    "毒品违禁": {
        "level": "block",
        "why": "吸毒、制毒、违禁药物属违法内容",
        "how": "剧情可「提及存在」但绝不写配方、剂量、制作或吸食过程；让角色承担法律与健康代价",
    },
    "教唆犯罪": {
        "level": "block",
        "why": "传授犯罪方法、教唆犯罪（含可复现的操作步骤）",
        "how": "只写结果、动机与追责，不写实施方法；保留调查、阻止、救援与代价",
    },
    "赌博": {
        "level": "block",
        "why": "赌博内容，尤其是具体玩法与赢钱技巧",
        "how": "不写赌术、赌局流程与盈利手法；冲突落在人物关系与后果上",
    },
    "自杀自残": {
        "level": "block",
        "why": "自杀自残的具体方法可诱发模仿",
        "how": "写情绪与人际支持，不写方法、工具、剂量；情节需要时给出求助与转机",
    },
    "隐私人肉": {
        "level": "block",
        "why": "泄露隐私、人肉搜索、网络暴力属严重违规",
        "how": "不使用真实姓名、住址、联系方式与可识别组合信息；冲突在虚构身份内解决",
    },
    "引流联系方式": {
        "level": "fix",
        "why": "正文出现真实联系方式、群号、二维码会被判引流，通常直接驳回",
        "how": "正文里一律不出现任何联系方式；作者互动（求票、请假、小剧场）请放到平台的「作者有话说」栏",
    },
    "正文作者话": {
        "level": "fix",
        "why": "求票求订、请假、作者吐槽等与情节无关的内容出现在正文里属违规",
        "how": "把这些内容从正文移出，发到「作者有话说」；正文只保留小说情节",
    },
    "广告交易": {
        "level": "fix",
        "why": "与小说无关的广告、商品推广、诱导线下交易、集资返利",
        "how": "现实品牌一律替换为虚构名；不出现购买号召、优惠、返利或投资承诺",
    },
    "乱码占位": {
        "level": "fix",
        "why": "乱码、符号填充、占位符属「批量无意义内容」",
        "how": "删除占位与符号行，把待补情节正式写完；符号只用于必要的分隔",
    },
    "重复填充": {
        "level": "fix",
        "why": "重复段落、凑字数属恶意水文，影响推荐",
        "how": "合并或删掉重复内容，用具体的事件推进替代复述",
    },
    "水文特征": {
        "level": "fix",
        "why": "流水账、无目标无结果、大段不分行，属质量门禁范围",
        "how": "每章保证「目标 → 行动 → 变化 → 结果/新问题」闭环；段落短小、手机阅读友好",
    },
    "绕审写法": {
        "level": "review",
        "why": "用拼音、字母、拆字、符号间隔替代敏感词，平台**明确禁止**，且会被判写作不规范",
        "how": "不要为躲审故意写不规范——平台禁止的是违规内容本身。直接按合规方向改写情节或表达",
    },
    "现实品牌": {
        "level": "review",
        "why": "真实品牌、商标、药名、机构名易判广告嫌疑或侵权",
        "how": "替换为虚构名（如「某品牌」「临江市第一中学」）或做泛化处理",
    },
    "涉黑违法": {
        "level": "fix",
        "why": "黑恶势力、高利贷、收保护费等题材，平台对鼓吹或美化违法特别敏感",
        "how": "写犯罪的代价与追责，不写组织运作细节，不把违法包装成捷径或爽点",
    },
    "暴力细节": {
        "level": "review",
        "why": "过度血腥、虐杀细节、可模仿的手段描写",
        "how": "写结果与情绪，不写操作细节；避免对弱者施暴的审美化，保留制止与代价",
    },
    "擦边描写": {
        "level": "review",
        "why": "挑逗性描写、身体部位反复特写等擦边内容，各平台尺度不同",
        "how": "用关系张力、对话与留白替代身体描写；免费平台（番茄/七猫）尺度更严",
    },
    "脏话低俗": {
        "level": "review",
        "why": "粗口与低俗用语影响观感，部分平台机审会拦",
        "how": "用「他骂了句粗话」带过，或软化表达；角色可以有锋芒，但不堆低俗词",
    },
    "现实原型": {
        "level": "review",
        "why": "真实案件、公众事件、真实人物的可识别映射涉及侵权与影射",
        "how": "改造素材（背景、时间、身份、动机均需变化），不能只改名字照搬",
    },
}

# ---- 词表：类别 → 词 ----
RULES: dict[str, list[str]] = {
    "涉政有害": [
        "颠覆国家政权", "颠覆政权", "分裂国家", "危害国家安全", "恐怖袭击", "恐怖组织",
        "极端组织", "圣战", "民族仇恨", "民族歧视",
    ],
    "邪教迷信": [
        "邪教", "全能神", "法轮功", "跳大神", "符水", "请仙", "招魂", "驱邪治病",
        "算命改命", "开天眼", "童子命", "包治百病", "大师治病",
    ],
    "淫秽色情": [
        "强奸", "轮奸", "乱伦", "恋童", "幼女", "幼齿", "性器官", "阴茎", "阴道",
        "精液", "淫水", "淫秽", "卖淫", "嫖娼", "群交", "春药", "迷奸", "猥亵",
    ],
    "未成年不当": ["未成年性行为", "学生妹", "萝莉控", "侵害未成年"],
    "毒品违禁": [
        "冰毒", "海洛因", "可卡因", "摇头丸", "大麻", "鸦片", "吗啡", "制毒",
        "贩毒", "吸毒", "毒枭", "麻古", "氯胺酮", "K粉",
    ],
    "教唆犯罪": [
        "制毒配方", "制作炸弹", "制造炸弹", "自制枪支", "制造枪械", "杀人步骤",
        "完美犯罪", "毁尸灭迹", "销赃渠道", "逃避追责的教程", "如何杀人",
    ],
    "赌博": [
        "赌场", "赌局", "赌资", "赌瘾", "赌术", "赌球", "出千", "押注",
        "百家乐", "网络赌博", "地下赌场", "非法博彩", "六合彩", "老虎机", "洗钱",
    ],
    "自杀自残": ["自杀方法", "安眠药自杀", "割腕", "上吊", "烧炭自杀", "教唆自杀"],
    "隐私人肉": ["人肉搜索", "开盒", "曝光住址", "身份证号", "家庭住址"],
    "引流联系方式": [
        "微信号", "微信公众号", "公众号", "QQ群", "扣扣群", "企鹅群", "加我微信",
        "加我QQ", "扫码", "二维码", "扫一扫", "私聊我", "加群", "关注我",
        "微博", "抖音号", "快手号", "小红书", "B站", "哔哩哔哩",
    ],
    "正文作者话": [
        "求收藏", "求推荐票", "求月票", "求订阅", "求打赏", "求票", "跪求",
        "投我一票", "作者有话说", "作者的话", "作者君", "小剧场", "请假条",
        "请假一天", "断更", "停更", "明天补更", "加更", "上架感言",
    ],
    "广告交易": [
        "优惠券", "限时折扣", "返利", "投资承诺", "加盟", "代购", "扫码购买",
        "点击链接", "下载APP",
    ],
    "现实品牌": [
        "可口可乐", "百事可乐", "麦当劳", "肯德基", "星巴克", "茅台", "老干妈",
        "淘宝", "支付宝", "拼多多", "美团", "滴滴出行", "王者荣耀", "安卓系统",
        "特斯拉", "奔驰", "宝马", "耐克", "阿迪达斯", "阿莫西林", "布洛芬", "头孢",
    ],
    "脏话低俗": [
        "他妈的", "妈的", "傻逼", "傻B", "煞笔", "贱人", "婊子", "王八蛋",
        "操你", "草你", "屌丝", "去死吧",
    ],
    "暴力细节": ["分尸", "碎尸", "肢解", "虐杀", "挖眼", "剥皮", "掏心"],
    "擦边描写": ["下体", "床戏", "肉体交易", "酥胸", "娇喘", "情欲", "欢爱"],
}

# ---- 类别别名：平台加严词库习惯用短类别名，统一映射到全局类别 ----
CATEGORY_ALIAS: dict[str, str] = {
    "政治": "涉政有害",
    "影射现实": "现实原型",
    "暴恐": "暴力细节",
    "涉黑": "涉黑违法",
    "色情": "淫秽色情",
    "赌博": "赌博",
    "违禁品": "毒品违禁",
    "耽美敏感": "擦边描写",
    "迷信": "邪教迷信",
    "引流": "引流联系方式",
}


def resolve_category(cat: str) -> str:
    """把平台词库里的短类别名归到全局类别；未知类别保守归入 review 档。"""
    if cat in CATEGORY_META:
        return cat
    if cat in CATEGORY_ALIAS:
        return CATEGORY_ALIAS[cat]
    return "现实原型"


# ---- 误报白名单：整词包含敏感词但语义无害 ----
# 只登记**真实会误报的**组合（写词表时最容易漏的一步）：
#   「大麻烦」含「大麻」、「下体育课」含「下体」、「骏马奔驰」含「奔驰」、
#   「宝马良驹」含「宝马」、「头孢」类药名在剧情里常作真实药名（仍需提示，不豁免）
# 命中词若其上下文出现白名单片段则跳过。
SAFE_COMPOUNDS: dict[str, list[str]] = {
    "大麻": ["大麻烦", "大麻雀"],
    "下体": ["下体育", "下体格", "下体质"],
    "奔驰": ["骏马奔驰", "奔驰而", "奔驰的", "飞奔驰", "奔驰在"],
    "宝马": ["宝马良驹", "宝马雕车"],
    "鸦片": ["鸦片战争"],  # 历史语境仍需谨慎，但非吸毒描写
    "二维码": ["二维码扫描器"],
    "关注我": [],
}

# ---- 模式规则：(类别, 正则, 说明) ----
PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "引流联系方式",
        re.compile(r"(?:QQ|qq|扣扣|企鹅)\s*(?:群|号)?\s*[:：]?\s*\d{5,12}"),
        "疑似 QQ 号/群号",
    ),
    (
        "引流联系方式",
        re.compile(r"(?:微信|weixin|WX|wx|VX|vx|V信|威信|徽信|卫星|扣扣)\s*[:：]?\s*[A-Za-z0-9_\-]{5,}"),
        "疑似微信号",
    ),
    ("引流联系方式", re.compile(r"\b1[3-9]\d{9}\b"), "疑似手机号"),
    ("引流联系方式", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,6}"), "邮箱地址"),
    ("引流联系方式", re.compile(r"(?:https?://|www\.)\S{4,}"), "外部链接"),
    (
        "乱码占位",
        re.compile(r"(?:\.{6,}|…{4,}|[*#\-=~_]{5,}|【?\s*(?:待[补充定写]|TODO|TBD|XXX|待填)\s*】?|此处省略|略去\d+字|本章未完)"),
        "占位符/乱码填充",
    ),
    ("乱码占位", re.compile(r"[\U0001F300-\U0001FAFF]{3,}"), "连续表情符号"),
    (
        "水文特征",
        re.compile(r"^[\s\W]{10,}$", re.M),
        "纯符号行/空白填充",
    ),
    (
        "绕审写法",
        # 中文夹拼音/字母：后面接中文或中文标点/句末都算
        re.compile(r"[\u4e00-\u9fff]([a-zāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]{2,8})(?=[\u4e00-\u9fff，。！？、；：“”‘’]|$)"),
        "中文句子中间夹拼音字母（平台禁止用拼音/字母替代敏感词）",
    ),
    (
        "绕审写法",
        re.compile(r"[\u4e00-\u9fff][*·\.\-_ ]{1,2}[\u4e00-\u9fff][*·\.\-_ ]{1,2}[\u4e00-\u9fff]"),
        "汉字之间用符号/空格间隔（疑似拆词绕审）",
    ),
    (
        "广告交易",
        re.compile(r"(?:扫描|长按|识别)\s*(?:下方|下面|图中)?\s*二维码"),
        "引导扫码",
    ),
    (
        "未成年不当",
        re.compile(r"(?:未成年|幼女|少女|学生|萝莉)[^。！？\n]{0,12}(?:性|裸|脱|床|侵犯|猥亵)"),
        "未成年 + 性相关组合（绝对红线）",
    ),
    (
        "水文特征",
        re.compile(r"[^\n]{500,}"),
        "超长不分段（手机阅读不友好，也常是水文特征）",
    ),
]


# 中文文本里正常出现的英文词（技术/常见缩写），避免拼音检测误报
SAFE_LATIN = {
    "python", "java", "app", "cpu", "gpu", "wifi", "ok", "ai", "ip", "id", "ui",
    "url", "api", "boss", "gdp", "pk", "cp", "vip", "led", "dna", "atm", "usa",
    "ceo", "cto", "hr", "pr", "qq", "pc", "usb", "gps", "nba", "cba", "gmv",
}


def _is_safe(text: str, pos: int, word: str) -> bool:
    """白名单：命中词的上下文里出现无害复合词则跳过。"""
    for safe in SAFE_COMPOUNDS.get(word, []):
        start = max(0, pos - len(safe))
        if safe in text[start : pos + len(word) + len(safe)]:
            return True
    return False


def _issue(category: str, detail: str, *, level: str | None = None, index: int = -1) -> dict:
    meta = CATEGORY_META[category]
    lv = level or meta["level"]
    return {
        "type": "敏感词" if category in RULES else "合规",
        "category": category,
        "level": lv,
        "severity": _LEVEL_SEVERITY[lv],
        "detail": detail,
        "why": meta["why"],
        "suggestion": meta["how"],
        "index": index,
    }


# 级别升档顺序：review → fix → block
_LEVEL_UP = {"review": "fix", "fix": "block", "block": "block"}


def _escalate(issue: dict, categories: list[str] | None) -> dict:
    """平台加严：同一处证据在更严的平台上等级更高（证据不变，处置升级）。"""
    if categories and issue["category"] in categories:
        issue["level"] = _LEVEL_UP[issue["level"]]
        issue["severity"] = _LEVEL_SEVERITY[issue["level"]]
        issue["detail"] = "【该平台加严】" + issue["detail"]
    return issue


def check(
    text: str,
    platform: str = "",
    custom_words: list[str] | None = None,
    extra_rules: dict[str, list[str]] | None = None,
    escalate: list[str] | None = None,
) -> list[dict]:
    """按类别扫描文本，返回分级风险列表（不含计分，计分由调用方合并）。

    platform：平台 key（用于附加平台加严口径的说明，不做差异化词表替换）。
    extra_rules：平台加严词表 {类别: [词]}，与内置词表合并。
    """
    issues: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (类别, 词) 去重，同一词只报一次

    rules: dict[str, list[str]] = {k: list(v) for k, v in RULES.items()}
    if extra_rules:
        for cat, words in extra_rules.items():
            target = resolve_category(cat)
            rules.setdefault(target, []).extend(words)

    if custom_words:
        rules.setdefault("自定义", []).extend(w for w in custom_words if w and w.strip())

    for cat, words in rules.items():
        for w in words:
            pos = text.find(w)
            if pos < 0:
                continue
            if _is_safe(text, pos, w):
                continue
            if (cat, w) in seen:
                continue
            seen.add((cat, w))
            if cat == "自定义":
                issues.append(
                    {
                        "type": "敏感词",
                        "category": "自定义",
                        "level": "fix",
                        "severity": "medium",
                        "detail": f"命中项目自定义敏感词「{w}」",
                        "why": "编辑点名禁用的词",
                        "suggestion": "按项目口径替换或删除",
                        "index": pos,
                    }
                )
            else:
                issues.append(_issue(cat, f"{cat}：「{w}」", index=pos))

    for cat, pat, note in PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        hit = m.group(0)
        # 正常英文词（Python/APP 等）不算「中文夹字母」
        if cat == "绕审写法":
            latin = "".join(c for c in hit if c.isascii() and c.isalpha()).lower()
            if latin and latin in SAFE_LATIN:
                continue
            # 仅「拆字间隔」那条要求必须含非空格符号（"他 说" 这类空格排版放过）；
            # 拼音那条不适用此判断（否则会把拼音命中全部误过滤掉）
            if "符号" in note and not re.search(r"[*·.\-_]", hit):
                continue
        issues.append(_issue(cat, f"{cat}：{note}（命中「{hit[:40]}」）", index=m.start()))

    # 重复段落（相邻两行高度相似且够长）——恶意水文常见形态
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 20]
    for i in range(1, len(lines)):
        if lines[i] == lines[i - 1]:
            issues.append(_issue("重复填充", f"相邻段落完全重复：「{lines[i][:30]}…」"))
            break

    # 平台加严升档（证据一致，处置按平台不同）
    for i in issues:
        _escalate(i, escalate)

    # 按严重程度排序，便于前端优先显示
    order = {"block": 0, "fix": 1, "review": 2}
    issues.sort(key=lambda i: (order.get(i["level"], 3), i.get("index", -1)))
    return issues


SUMMARY = """按平台公开口径分为三档：block 硬红线、fix 影响审核/推荐、review 需人工判断语境。
不存在「敏感词清零即合规」，请结合情节功能与可复现性判断；本检测不保证平台过审结果。"""


# ---- 报告排版（导出报告共用一份格式，避免各处自己拼字符串）----
LEVEL_MARKS = {
    "block": "🔴 硬红线",
    "fix": "🟠 影响审核",
    "review": "🔵 待复核",
}


def format_issues(issues: list[dict], indent: str = "  ") -> list[str]:
    """按级别分组排版 issues，每行含类别、命中原词与合规改法。

    改法只在每组首次出现时重复（同类别的 why/how 相同，不必每行都印）。
    """
    if not issues:
        return []
    order = ["block", "fix", "review"]
    lines: list[str] = []
    for level in order:
        group = [i for i in issues if i.get("level") == level]
        if not group:
            continue
        lines.append(f"{LEVEL_MARKS.get(level, level)}（{len(group)} 项）")
        seen_cat: set[str] = set()
        for i in group:
            cat = i.get("category", i.get("type", "问题"))
            lines.append(f"{indent}· [{cat}] {i.get('detail', '')}")
            if cat not in seen_cat:
                seen_cat.add(cat)
                if i.get("why"):
                    lines.append(f"{indent}    为何踩雷：{i['why']}")
                if i.get("suggestion"):
                    lines.append(f"{indent}    合规改法：{i['suggestion']}")
        lines.append("")
    return lines


def summarize(issues: list[dict], top: int = 6) -> list[str]:
    """类别汇总：按出现次数排序，指出最该先处理的类别。"""
    if not issues:
        return []
    counts: dict[str, int] = {}
    for i in issues:
        cat = i.get("category", "其他")
        counts[cat] = counts.get(cat, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    parts = [f"{cat} {n} 项" for cat, n in ranked]
    return ["问题类别汇总：" + "；".join(parts)]


def verdict(avg_score: float, block_count: int) -> str:
    """给出结论：有硬红线时无论平均分多高都不能投。"""
    if block_count > 0:
        return f"❌ 存在 {block_count} 项硬红线，必须先删改；分数不是依据——红线项只要在，就不宜投稿"
    if avg_score >= 90:
        return "✅ 未发现硬红线且平均分 ≥90，可投（仍以平台实际审核为准）"
    return "⚠️ 未发现硬红线，但平均分低于 90，建议修稿后再投"
