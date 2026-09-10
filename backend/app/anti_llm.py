"""去 AI 味引擎（M3 · 借鉴自美智子 webnovel-master 的 webnovel-anti-llm 模块与
inkos 项目设计，经上官婉儿分享；北斗改编实现——纯 Python 确定性检测 + LLM 改写）。

三层防线：
1. 源头控制：ANTI_LLM_RULES 注入写作 prompt（写前规避）
2. 确定性检测：detect() 零成本评分（疲劳词密度/禁用句式/句长波动/对话标签比/段落均匀度）
3. AI 改写：deflavor 改写 prompt（检测不过时用）
"""

from __future__ import annotations

import re

# ---------- 疲劳词表（AI 高频词 → 替代词；改编自 webnovel-anti-llm，有增删） ----------
FATIGUE_WORDS: dict[str, str] = {
    "突然": "猛地/骤然/倏忽",
    "非常": "极其/格外/尤为",
    "特别": "格外/尤为/尤其",
    "十分": "颇为/相当",
    "真的": "着实/的确",
    "其实": "说来/说白了",
    "所以": "于是/故而",
    "然后": "接着/随后/继而",
    "立刻": "当即/马上",
    "瞬间": "一刹/顷刻",
    "瞬间爆发": "轰然炸开",
    "似乎": "仿佛/貌似",
    "显然": "明摆着/看得出",
    "然而": "可/不过",
    "总之": "说白了",
    "此时此刻": "这会儿/此刻",
    "内心深处": "心底",
    "不由得": "忍不住",
    "瞬间明白": "回过味来",
    "瞳孔骤缩": "瞳孔一缩",
    "嘴角勾起一抹弧度": "嘴角一歪",
    "眼中闪过一丝": "眼里掠过",
    "空气仿佛凝固": "空气凝住了",
}

# ---------- 禁用句式（正则，典型 AI 特征） ----------
BANNED_PATTERNS: list[tuple[str, str]] = [
    (r"首先.{0,30}其次.{0,30}(最后|再次)", "首先…其次…最后（议论文腔）"),
    (r"(总的来说|总而言之)[，,]", "总起句"),
    (r"(值得注意的是|值得一提的是)", "提请注意句"),
    (r"(从某种意义上(说|来讲))", "抽象限定句"),
    (r"(不言而喻|显而易见|众所周知)[，,]?", "套话"),
    (r"(这说明|这意味着|由此可见)[，,]?", "过度总结"),
    (r"(在这个.{1,6}的时代)", "宏大空话"),
    (r"(仿佛在诉说着?)", "滥用拟人"),
]

# ---------- 写作 prompt 注入的源头规则（精简版） ----------
ANTI_LLM_RULES = """【文风铁律（去 AI 味）】
- 禁用这些高频 AI 词：突然、非常、十分、其实、显然、瞬间、立刻、不由得、内心深处；改用更具体的动作或口语。
- 禁止"首先…其次…最后"等议论文句式；禁止"值得注意的是""显而易见""这说明""仿佛在诉说"。
- 少用"眼中闪过一丝""嘴角勾起一抹弧度""空气仿佛凝固"这类模板化描写。
- 对话占 30%-40%，用对话推剧情；"他说/她说"类对话标签尽量少，改用动作、神态带出说话人。
- 句子长短交错，短句为主；段落长度参差（重要场景段落长、过渡段落短）。
- 多用白描和具体细节，不解释情绪（写"他把手里的杯子攥紧了"，不写"他非常紧张"）。"""


def detect(text: str) -> dict:
    """确定性 AI 味检测，返回 {score, issues}。纯本地计算，不调 LLM。"""
    issues: list[dict] = []
    if len(text) < 50:
        return {"score": 100, "issues": [{"type": "太短", "detail": "文本过短，跳过检测"}]}

    score = 100.0

    # 1. 疲劳词密度（按出现次数 / 千字）
    total = len(text)
    fatigue_hits: dict[str, int] = {}
    for w in FATIGUE_WORDS:
        n = text.count(w)
        if n:
            fatigue_hits[w] = n
    fatigue_count = sum(fatigue_hits.values())
    fatigue_density = fatigue_count / total * 1000
    if fatigue_density > 8:
        score -= 20
        top = sorted(fatigue_hits.items(), key=lambda x: -x[1])[:5]
        issues.append({
            "type": "疲劳词",
            "severity": "high",
            "detail": f"疲劳词密度 {fatigue_density:.1f}/千字（阈值 8），最高频：{ '、'.join(f'{w}×{n}' for w, n in top) }",
        })
    elif fatigue_density > 4:
        score -= 10
        top = sorted(fatigue_hits.items(), key=lambda x: -x[1])[:3]
        issues.append({
            "type": "疲劳词",
            "severity": "low",
            "detail": f"疲劳词密度 {fatigue_density:.1f}/千字偏高：{ '、'.join(f'{w}×{n}' for w, n in top) }",
        })

    # 2. 禁用句式
    banned_hits: list[str] = []
    for pat, name in BANNED_PATTERNS:
        if re.search(pat, text):
            banned_hits.append(name)
    if banned_hits:
        score -= min(30, 6 * len(banned_hits))
        issues.append({
            "type": "禁用句式",
            "severity": "high" if len(banned_hits) >= 3 else "low",
            "detail": "命中：" + "、".join(banned_hits),
        })

    # 3. 句长波动（标准差 < 5 → 单调）
    sentences = [s for s in re.split(r"[。！？!?…]+", text) if s.strip()]
    if len(sentences) >= 10:
        lens = [len(s.strip()) for s in sentences]
        mean = sum(lens) / len(lens)
        std = (sum((x - mean) ** 2 for x in lens) / len(lens)) ** 0.5
        if std < 5:
            score -= 12
            issues.append({
                "type": "句式单调",
                "severity": "medium",
                "detail": f"句长标准差 {std:.1f}（<5 单调），句子长短太一致",
            })

    # 4. 对话标签比（"他说"类密度）
    dialogue = re.findall(r"[「“][^」”]{1,50}[」”]", text)
    said_tags = len(re.findall(r"(说道?|问道?|答道?|喊道?|笑道?|冷冷道)", text))
    tag_ratio = said_tags / max(len(dialogue), 1)
    if len(dialogue) >= 5 and tag_ratio > 0.5:
        score -= 10
        issues.append({
            "type": "对话标签",
            "severity": "medium",
            "detail": f"{len(dialogue)} 句对话中 {said_tags} 句带「说道」类标签（占比 {tag_ratio:.0%}），改用动作带出说话人",
        })

    # 5. 段落均匀度
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if len(paras) >= 6:
        plens = [len(p) for p in paras]
        pmean = sum(plens) / len(plens)
        pstd = (sum((x - pmean) ** 2 for x in plens) / len(plens)) ** 0.5
        if pstd < 10:
            score -= 8
            issues.append({
                "type": "段落均匀",
                "severity": "low",
                "detail": f"段落长度标准差 {pstd:.1f}（<10），段落节奏太均匀",
            })

    return {"score": max(0, round(score)), "issues": issues}


def deflavor_rewrite_prompt(text: str, report: dict) -> str:
    """AI 改写 prompt：针对检测报告逐项修复，保持剧情与字数。"""
    issue_lines = "\n".join(f"- [{i.get('type')}] {i.get('detail')}" for i in report.get("issues", [])) or "-（无具体问题）"
    return (
        "以下是网文章节正文，去 AI 味检测未达标。请改写：\n\n"
        f"【检测报告】\n{issue_lines}\n\n"
        f"{ANTI_LLM_RULES}\n\n"
        "【改写要求】\n"
        "- 保持剧情、对话信息、情节顺序完全不变\n"
        "- 字数与原文相当（±15%）\n"
        "- 只改表达：替换疲劳词、拆掉模板句式、让句长和段落节奏更自然\n"
        "- 直接输出改写后的正文纯文本，不要任何解释\n\n"
        f"【原文】\n{text[:8000]}"
    )
