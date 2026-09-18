"""技能卡：内置网文创作技能卡（SKILL.md + 参考文件），作为 AI 面板的快捷指令。

卡片来源：novel-skill-cards 仓库。每张卡是一个**完整包**：

    skillcards/<slug>/SKILL.md              工作手册（YAML frontmatter + 正文）
    skillcards/<slug>/references/*.md       分维度检查清单/框架（正文里按维度指定加载）
    skillcards/<slug>/assets/*.md           产出模板（如拆书报告模板）
    skillcards/<slug>/scripts/*.py          辅助脚本（**本环境不可执行**，见下）

## 为什么需要这个模块做「加载」

技能卡正文写的是「按分析维度速查表加载 `references/xxx.md`」——那是给**能读文件
的 agent** 写的流程。北斗是 web 应用，模型看不到磁盘，所以只把 SKILL.md 塞进
prompt 的话，那些检查清单**永远不会被加载**，卡里「所有结论必须标注章节依据」
之类的质量要求就只能靠模型自觉（甚至可能谎称已加载）。

这里的职责就是把「按维度加载参考文件」在服务端真实执行：
解析卡内速查表 → 按用户任务关键词挑选 → 全文注入 prompt（超限显式标注）。

## 脚本为什么不执行

卡里还写了 `python scripts/chapter_splitter.py <文件>` 这类步骤。本环境不执行
仓库内脚本（安全面太大），因此 prompt 里**明确声明脚本不可执行**，并要求模型
依据文本完成等价分析——避免它谎称「已运行脚本」并编造输出。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_ai_config, get_current_user, get_owned_novel
from ..models import User
from .ai import SYSTEM_PROMPT, _novel_context, _stream_openai

router = APIRouter(prefix="/api/skills", tags=["skills"])

CARD_DIR = Path(__file__).parent.parent / "skillcards"

# 参考文件扫描目录
DOC_DIRS = ("references", "assets", "scripts")

# 注入上限：卡包整体很小（最大的拆书卡 6 个参考约 2.4 万字），但仍设硬上限，
# 防止将来有人丢进大文件把上下文撑爆。超限时**显式截断并标注**，不静默丢弃。
MAX_DOC_CHARS = 30_000
MAX_TOTAL_DOC_CHARS = 60_000

# slug → (显示名, 分类, 一句话用途)
CARD_META: dict[str, tuple[str, str, str]] = {
    "outline-architect": ("大纲架构师", "create", "主线/升级线/伏笔三线合一，卷结构+章级细纲"),
    "worldbuilding-bible": ("设定集构建", "create", "力量体系、势力、地理、经济，产出世界观圣经"),
    "opening-crafter": ("开篇打磨", "create", "黄金三章设计与诊断，立人/立困/立钩"),
    "chapter-hook-crafter": ("章节卡点", "create", "六大章末钩子，断点位置+结尾改写"),
    "name-forge": ("取名炉", "create", "人名/地名/功法/势力批量生成，附寓意与筛查"),
    "book-packager": ("文案包装", "create", "书名/简介/标签/章节名，按平台范式产出"),
    "novel-consistency-checker": ("穿帮检查", "check", "数值/设定口径/细节连续三层核对"),
    "webnovel-pace-analyzer": ("节奏分析", "check", "逐章扫描钩子与爽点，定位爽点荒与水文区"),
    "deai-rewrite": ("去AI味改写", "check", "AI高频词预警 + 六类AI腔改写"),
    "style-fingerprint": ("文风指纹", "check", "句长/对话占比/高频词测量，用于续写对齐"),
    "novel-deconstruction": ("拆书分析", "check", "黄金三章/情节/人物/世界观/爽点五维拆解"),
}

CATEGORY_LABEL = {"create": "创作生产", "check": "诊断改稿"}

# 反引号里的卡包内文件路径
_RE_DOC_REF = re.compile(r"`((?:references|assets|scripts)/[^`]+)`")
# 「必须/强制」判定：文件名前后各 60 字的窗口内出现这些词 → 无条件加载。
# 注意要双向匹配——卡里写的是「`references/x.md` …都必须加载执行」（文件名在前）。
_MUST_WORDS = ("必须", "强制", "任何任务", "任何范围")
# 速查表里表示「这次要全部加载」的标记
_ALL_MARKERS = ("全部加载", "全部参考", "所有参考")


def _card_file(slug: str) -> Path | None:
    """定位卡文件：优先目录布局 <slug>/SKILL.md，兼容单文件卡 <slug>.md。"""
    pkg = CARD_DIR / slug / "SKILL.md"
    if pkg.exists():
        return pkg
    flat = CARD_DIR / f"{slug}.md"
    return flat if flat.exists() else None


def _parse_frontmatter(text: str) -> tuple[str, str]:
    """拆出 (frontmatter, body)。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if m:
        return m.group(1), m.group(2).strip()
    return "", text.strip()


def _description(fm: str) -> str:
    """取 frontmatter 的 description 首行（兼容 folded YAML）。"""
    dm = re.search(r"^description:\s*>?\s*\n((?:[ \t]+.+\n?)+)", fm, re.M)
    if dm:
        return dm.group(1).strip().splitlines()[0].strip()
    dm2 = re.search(r"^description:\s*(.+)$", fm, re.M)
    return dm2.group(1).strip() if dm2 else ""


def _doc_title(path: Path, body: str) -> str:
    """参考文件标题：首个一级标题，退化到文件名。"""
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return m.group(1).strip() if m else path.stem


def _parse_card(slug: str) -> dict | None:
    path = _card_file(slug)
    if path is None:
        return None
    fm, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    zh, cat, brief = CARD_META.get(slug, (slug, "create", ""))
    return {
        "slug": slug,
        "name": zh,
        "category": cat,
        "category_label": CATEGORY_LABEL[cat],
        "brief": brief,
        "description": _description(fm)[:120],
        "body": body,
    }


def list_docs(slug: str) -> list[dict]:
    """列出卡包内参考文件（不含正文）：rel / title / kind / chars。"""
    pkg = CARD_DIR / slug
    out: list[dict] = []
    if not pkg.is_dir():
        return out
    for kind in DOC_DIRS:
        d = pkg / kind
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            out.append(
                {
                    "rel": f"{kind}/{p.name}",
                    "title": _doc_title(p, text) if p.suffix == ".md" else p.name,
                    "kind": kind,
                    "chars": len(text),
                    "executable": False if kind == "scripts" else None,
                }
            )
    return out


def core_docs(slug: str) -> list[str]:
    """卡正文里声明「必须/强制」都要加载的文件（无条件注入）。

    双向匹配：卡里可能写「`references/x.md` 任何任务都必须加载」，也可能写
    「必须加载 `references/x.md`」——只认一个方向会漏。
    """
    card = _parse_card(slug)
    if card is None:
        return []
    exist = {d["rel"] for d in list_docs(slug)}
    found: list[str] = []
    for line in card["body"].splitlines():
        refs = _RE_DOC_REF.findall(line)
        if refs and any(w in line for w in _MUST_WORDS):
            for rel in refs:
                if rel in exist and rel not in found:
                    found.append(rel)
    return found


# 速查表「全部加载」哨兵关键词：命中即加载该卡全部 references
ALL_SENTINEL = "\u0000ALL"


def _row_keywords(body: str) -> dict[str, list[str]]:
    """解析速查表：文件 → 对应关键词（按任务文本匹配用）。

    卡里的表形如 `| 开篇、前三章、黄金三章 | references/golden-three-chapters.md |`，
    关键词取该行第一格，按顿号/逗号/斜杠切开。
    """
    out: dict[str, list[str]] = {}
    for line in body.splitlines():
        if "|" not in line:
            continue
        refs = _RE_DOC_REF.findall(line)
        if not refs:
            continue
        cells = [c.strip() for c in line.split("|")]
        # 数据行形如「| 关键词… | `references/x.md` |」，去掉首尾空单元格后第一格是关键词
        kw_cell = next((c for c in cells if c), "")
        kws = [k.strip() for k in re.split(r"[、,，/]", kw_cell) if k.strip()]
        for rel in refs:
            entry = out.setdefault(rel, [])
            if kws:
                entry.extend(kws)
            # 「全书完整拆书 | 全部加载 + 模板」→ 标记该行要全部加载
            if any(mark in line for mark in _ALL_MARKERS):
                entry.append(ALL_SENTINEL)
    return out


def select_docs(slug: str, instruction: str = "", requested: list[str] | None = None) -> list[str]:
    """决定本次加载哪些参考文件。

    1. 用户显式指定 → 按指定（非法项忽略，避免旧前端/旧书签把接口打挂）；
    2. 否则按卡内速查表关键词匹配任务文本；
    3. 卡里声明「必须加载」的文件无条件加入；
    4. 一个维度都没命中（如用户只说「用这个技能开始工作」，AI 面板默认就是这样）
       → 全量加载。只给强制项的话，五个维度的检查清单全部缺席，卡等于废的。
    """
    docs = list_docs(slug)
    if not docs:
        return []
    known = {d["rel"] for d in docs}
    if requested:
        return sorted({r for r in requested if r in known} | set(core_docs(slug)))

    card = _parse_card(slug)
    if card is None:
        return []
    body = card["body"]
    text = instruction or ""
    picked: set[str] = set(core_docs(slug))
    matched_any = False

    for rel, kws in _row_keywords(body).items():
        if rel not in known:
            continue
        plain = [kw for kw in kws if kw and kw != ALL_SENTINEL]
        if not any(kw in text for kw in plain):
            continue
        matched_any = True
        picked.add(rel)
        # 该行标注「全部加载」（如「全书完整拆书」）→ 连同全部 references 一起加载
        if ALL_SENTINEL in kws:
            picked |= {d["rel"] for d in docs if d["kind"] == "references"}

    if not matched_any:
        picked |= {d["rel"] for d in docs if d["kind"] in ("references", "assets")}
    return sorted(picked)


def docs_block(slug: str, rels: list[str]) -> tuple[str, list[dict], list[str]]:
    """把参考文件正文拼成可注入文本块。

    返回 (文本, 已加载清单, 加载说明)。超限**显式标注**——静默截断会让模型以为
    自己看到了全文，是最坏的情况。
    """
    docs = {d["rel"]: d for d in list_docs(slug)}
    pkg = CARD_DIR / slug
    chunks: list[str] = []
    loaded: list[dict] = []
    notes: list[str] = []
    total = 0

    for rel in rels:
        meta = docs.get(rel)
        if meta is None or meta["kind"] == "scripts":
            # 脚本正文不注入：不可执行，注入了只会诱导模型假装在跑
            continue
        try:
            text = (pkg / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if total >= MAX_TOTAL_DOC_CHARS:
            notes.append(f"{rel} 未加载（已达本次注入上限 {MAX_TOTAL_DOC_CHARS} 字）")
            continue
        truncated = False
        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS]
            truncated = True
            notes.append(f"{rel} 超长，已截断至 {MAX_DOC_CHARS} 字")
        if total + len(text) > MAX_TOTAL_DOC_CHARS:
            allowed = MAX_TOTAL_DOC_CHARS - total
            text = text[:allowed]
            truncated = True
            notes.append(f"{rel} 因总量上限截断至 {allowed} 字")
        total += len(text)
        loaded.append({"rel": rel, "title": meta["title"], "chars": len(text), "truncated": truncated})
        tail = "\n\n……（该文件超过注入上限，此处已截断）" if truncated else ""
        chunks.append(f"<<<FILE {rel}>>>\n{text}{tail}\n<<<END FILE>>>")

    if not loaded:
        return "", [], notes

    head = "【已加载的参考清单（技能卡正文要求按维度加载的文件，全文附在下方）】\n" + "\n".join(
        f"- {d['rel']}（{d['chars']:,} 字）{d['title']}" for d in loaded
    )
    return head + "\n\n【参考文件全文】\n" + "\n\n".join(chunks), loaded, notes


def _scripts_note(slug: str) -> str:
    """脚本不可执行声明——防止模型谎称「已运行脚本」并编造输出。"""
    scripts = [d["rel"] for d in list_docs(slug) if d["kind"] == "scripts"]
    if not scripts:
        return ""
    return (
        "【脚本说明】本技能附带以下脚本：" + "、".join(scripts) + "。"
        "**当前环境无法执行这些脚本**（模型无文件系统与命令行）。请直接依据给定文本完成"
        "等价分析，并在报告中说明结论来自阅读原文；**不要声称已运行脚本**，也不要编造脚本输出。\n"
    )


def card_block(
    slug: str,
    task: str = "",
    requested: list[str] | None = None,
    tool_output: str = "",
) -> str:
    """组装「技能卡包」文本块：工作手册 + 参考文件全文 + 加载说明 + 工具输出。

    /skills/run 与 /ai/chat 共用——两处各拼一遍是过去易漂移的地方。
    """
    card = _parse_card(slug)
    if card is None:
        raise HTTPException(404, "技能卡不存在")
    rels = select_docs(slug, instruction=task, requested=requested)
    block, loaded, notes = docs_block(slug, rels)
    loaded_rels = {d["rel"] for d in loaded}
    skipped = [d["rel"] for d in list_docs(slug) if d["rel"] not in loaded_rels and d["rel"] not in rels]

    parts = [
        f"技能卡「{card['name']}」的完整工作手册，请严格遵循其中的流程与标准执行：",
        "",
        "---",
        card["body"],
        "---",
        "",
    ]
    if block:
        parts += [block, ""]
    if tool_output:
        parts += [tool_output, ""]
    if skipped:
        parts += ["【本次未加载的文件】" + "、".join(skipped) + "（如确需，可要求加载）", ""]
    if notes:
        parts += ["【加载说明】" + "；".join(notes), ""]
    sn = _scripts_note(slug)
    if sn:
        parts.append(sn)
    return "\n".join(parts)


def build_skill_prompt(
    slug: str,
    context: str,
    task: str,
    requested: list[str] | None = None,
    tool_output: str = "",
) -> str:
    """技能卡一次性执行 prompt（/skills/{slug}/run 用）。"""
    block = card_block(slug, task=task, requested=requested, tool_output=tool_output)
    return f"{block}\n当前作品信息：\n{context}\n\n任务：{task}"


async def novel_texts(db: AsyncSession, novel_id: int, limit: int = 40) -> list[tuple[str, str]]:
    """取本书最近若干章正文（纯文本），供技能卡附带脚本在服务端实测。

    只取有正文的章；按 sort_order 倒序取最近的，再正序返回（保持阅读顺序）。
    """
    from sqlalchemy import func, select

    from ..models import Chapter
    from ..utils import strip_html

    rows = (
        await db.execute(
            select(Chapter)
            .where(Chapter.novel_id == novel_id, func.length(Chapter.content) > 0)
            .order_by(Chapter.sort_order.desc())
            .limit(limit)
        )
    ).scalars().all()
    out: list[tuple[str, str]] = []
    for c in reversed(rows):
        text = strip_html(c.content or "").strip()
        if text:
            out.append((c.title or f"第{c.sort_order}章", text))
    return out


def tool_output_for(slug: str, chapters: list[tuple[str, str]]) -> str:
    """该卡若有可执行工具，则实测并返回工具输出（无工具/无正文返回空串）。"""
    from ..skilltools import run_for_card

    if not chapters:
        return ""
    return run_for_card(slug, chapters)


@router.get("")
async def list_skills(user: User = Depends(get_current_user)):
    """技能卡列表（含参考文件清单，供前端显示「本次会加载哪些参考」）。"""
    out = []
    for slug in CARD_META:
        card = _parse_card(slug)
        if not card:
            continue
        item = {k: card[k] for k in ("slug", "name", "category", "category_label", "brief", "description")}
        item["docs"] = list_docs(slug)
        item["core_docs"] = core_docs(slug)
        out.append(item)
    return out


@router.get("/{slug}")
async def get_skill_detail(slug: str, user: User = Depends(get_current_user)):
    card = _parse_card(slug)
    if card is None:
        raise HTTPException(404, "技能卡不存在")
    return {**card, "docs": list_docs(slug), "core_docs": core_docs(slug)}


def get_card(slug: str) -> dict | None:
    """按 slug 取技能卡（供 AI 对话挂接使用）。"""
    return _parse_card(slug)


class SkillRunIn(BaseModel):
    novel_id: int
    chapter_id: int | None = None
    instruction: str = Field(default="", max_length=2000)
    config_id: int | None = None
    # 本次要加载的参考文件（rel 路径）；留空 = 按任务关键词自动选择
    docs: list[str] | None = Field(default=None, max_length=40)


@router.post("/{slug}/run")
async def run_skill(
    slug: str, data: SkillRunIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    card = _parse_card(slug)
    if card is None:
        raise HTTPException(404, "技能卡不存在")

    novel = await get_owned_novel(data.novel_id, user, db)
    config = await get_ai_config(user, db, data.config_id)
    context = await _novel_context(novel, db, data.chapter_id)

    task = data.instruction.strip() or f"请运用「{card['name']}」技能，基于以上作品信息开始工作，并主动给出产出。"
    tool_out = tool_output_for(slug, await novel_texts(db, novel.id))
    prompt = build_skill_prompt(slug, context, task, data.docs, tool_output=tool_out)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    return await _stream_openai(config, messages)
