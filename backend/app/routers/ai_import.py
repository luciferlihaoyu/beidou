"""AI 工厂 M4：导入续写（借鉴 webnovel-master 的 webnovel-import 模块——
从已有文本导入并逆向工程真相文件，导入后无缝接续创作；北斗改编实现）。

流程：
1. 分章：第X章 / Chapter N / 第X回 等常见网文标题行正则
2. 建项目 + Novel（[AI] 前缀）+ 默认卷 + 章节 + AiChapterJob(done)
3. 逆向工程真相文件（SSE 事件流，全程非流式 LLM 调用）：
   - 分批提取：每批 ≤4 章（各截 3000 字）→ 角色/伏笔/资源/摘要 JSON
   - 汇总合成：全书摘要 + 角色卡入库 + 伏笔台账 + 资源账本
4. 项目直接落到 writing 状态——导入即可点「批量连跑」续写。

成本护栏：文本 ≤ 60 万字、章节 ≤ 300；提取批每批 ≤4 章。
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import count_words, get_current_user
from ..models import AiChapterJob, AiProject, Chapter, Character, Novel, User, Volume, WorldviewEntry
from ..utils import strip_html
from .ai_factory import (
    _SYSTEM,
    _chat_text,
    _parse_json,
    _pick_config,
    _text_to_html,
)

router = APIRouter(prefix="/api/ai-factory", tags=["ai-factory"])

# 网文章节标题常见模式（借鉴 webnovel-import 的分割模式）
_CHAPTER_PATTERNS = [
    r"^\s*第[零一二三四五六七八九十百千万两\d]+章[^\n]*$",
    r"^\s*第[零一二三四五六七八九十百千万两\d]+回[^\n]*$",
    r"^\s*[Cc]hapter\s*\d+[^\n]*$",
    r"^\s*第[零一二三四五六七八九十百千万两\d]+节[^\n]*$",
    r"^\s*卷?\s*\d{1,4}[、.．]\s*\S[^\n]*$",
]
_CHAPTER_RE = re.compile("|".join(f"(?:{p})" for p in _CHAPTER_PATTERNS), re.M)


def _split_chapters(text: str) -> list[dict]:
    """按标题行分章。找不到标题行时按 4000 字硬切。"""
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        chunks = []
        step = 4000
        for i in range(0, len(text), step):
            part = text[i : i + step].strip()
            if part:
                chunks.append({"title": f"第{len(chunks) + 1}章", "content": part})
        return chunks
    chapters = []
    # 第一个标题前的内容（楔子/序章）
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        chapters.append({"title": "序章", "content": text[: matches[0].start()].strip()})
    for i, m in enumerate(matches):
        title = m.group(0).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            chapters.append({"title": title, "content": content})
    return chapters


class ImportIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    genre: str = Field(default="", max_length=50)
    style_notes: str = Field(default="", max_length=500)
    text: str = Field(min_length=500)  # 全文文本（前端从 txt 文件读出）
    target_chapter_words: int | None = Field(default=None, ge=200, le=20000)


@router.post("/projects/import")
async def import_novel(data: ImportIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """导入已有书稿 → 逆向真相文件 → 落到可续写状态。SSE 事件流报进度。"""
    plain = data.text.strip()
    if len(plain) > 600_000:
        raise HTTPException(400, "单次导入最多 60 万字；更长的书请分批导入（后续支持续导）")
    chapters = _split_chapters(plain)
    if not chapters:
        raise HTTPException(400, "未能识别章节结构（支持「第X章/第X回/Chapter N」标题行；无标题将按 4000 字硬切）")
    if len(chapters) > 300:
        raise HTTPException(400, f"识别出 {len(chapters)} 章，单次导入最多 300 章")
    # 单章过长截断保护（防异常粘贴）
    for ch in chapters:
        if len(ch["content"]) > 20000:
            ch["content"] = ch["content"][:20000]

    async def generate():
        def sse(ev: dict) -> str:
            return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

        try:
            # ---- 1. 建项目 + Novel + 章节 + jobs ----
            novel = Novel(user_id=user.id, title=f"[AI] {data.title}", genre=data.genre)
            db.add(novel)
            await db.flush()
            volume = Volume(novel_id=novel.id, title="导入卷", sort_order=0)
            db.add(volume)
            await db.flush()

            project = AiProject(
                user_id=user.id,
                novel_id=novel.id,
                status="writing",
                seed_prompt=f"导入续写：《{data.title}》",
                genre=data.genre,
                style_notes=data.style_notes,
                target_chapter_words=data.target_chapter_words,
            )
            db.add(project)
            await db.flush()

            from ..search_fts import sync_chapter

            total_words = 0
            for i, ch in enumerate(chapters):
                html = _text_to_html(ch["content"])
                wc = count_words(html)
                total_words += wc
                chapter = Chapter(
                    novel_id=novel.id,
                    volume_id=volume.id,
                    title=ch["title"],
                    content=html,
                    word_count=wc,
                    sort_order=i,
                    status="writing",
                )
                db.add(chapter)
                await db.flush()
                db.add(
                    AiChapterJob(
                        project_id=project.id,
                        chapter_id=chapter.id,
                        status="done",
                        outline="",
                        actual_words=wc,
                        attempt=1,
                    )
                )
                try:
                    await sync_chapter(db, chapter.id)
                except Exception:  # noqa: BLE001
                    pass
            novel.word_count = total_words
            await db.commit()
            yield sse({"event": "split", "project_id": project.id, "chapters": len(chapters), "total_words": total_words})

            # ---- 2. 逆向工程：分批提取 ----
            llm = await _pick_config(user, db, project.summary_llm)
            batch_size = 4
            batches = [chapters[i : i + batch_size] for i in range(0, len(chapters), batch_size)]
            batch_notes: list[dict] = []
            for bi, batch in enumerate(batches):
                yield sse({"event": "extract", "batch": bi + 1, "total_batches": len(batches)})
                joined = "\n\n".join(
                    f"【{c['title']}】\n{c['content'][:3000]}" for c in batch
                )
                prompt = (
                    f"以下是网文《{data.title}》的第 {bi * batch_size + 1}-{bi * batch_size + len(batch)} 章正文节选。"
                    "请提取结构化信息，只输出 JSON：\n"
                    "{\n"
                    '  "summary": "本批章节剧情摘要（≤300字）",\n'
                    '  "characters": [{"name": "角色名", "role": "主角|女主|配角|反派", "brief": "一句话特征"}],\n'
                    '  "hooks": [{"title": "未回收伏笔/悬念", "note": "一句话"}],\n'
                    '  "resources": "本批金钱/物品/等级等数值变化（无则空字符串）",\n'
                    '  "worldview": [{"category": "势力|地理|历史|规则|其他", "title": "条目名", "content": "一句话"}]\n'
                    "}\n要求：只提取本批新出现的角色/伏笔/条目；角色名用原文称呼。"
                )
                try:
                    raw = await _chat_text(llm, _SYSTEM, prompt, max_tokens=2000)
                    note = _parse_json(raw)
                    if isinstance(note, dict):
                        batch_notes.append(note)
                except Exception:  # noqa: BLE001  单批失败不致命，继续下一批
                    yield sse({"event": "extract_warn", "batch": bi + 1, "message": "本批提取失败，跳过（不影响后续）"})

            # ---- 3. 汇总合成：全局摘要 + 台账 + 资源账本 ----
            yield sse({"event": "merge"})
            merge_prompt = (
                f"网文《{data.title}》全书分批评提取结果如下（JSON 数组）：\n"
                f"{json.dumps(batch_notes, ensure_ascii=False)[:8000]}\n\n"
                "请汇总成全书级真相文件，只输出 JSON：\n"
                "{\n"
                '  "global_summary": "全书前情摘要（≤1000字，按时间线）",\n'
                '  "character_state": [{"name": "角色名", "location": "最近所在", "goal": "当前目标", "condition": "状态", "change": "最近变化"}],\n'
                '  "plot_arcs": [{"title": "伏笔/情节弧", "status": "埋设中|推进中|已回收", "note": "进展"}],\n'
                '  "particle_ledger": "资源账本：金钱/关键物品/等级当前状态（≤400字）"\n'
                "}\n只保留最新状态；已回收伏笔标已回收。"
            )
            try:
                raw = await _chat_text(llm, _SYSTEM, merge_prompt, max_tokens=3000)
                merged = _parse_json(raw)
                if isinstance(merged, dict):
                    if merged.get("global_summary"):
                        project.global_summary = str(merged["global_summary"])[:3000]
                    if isinstance(merged.get("character_state"), list):
                        project.character_state = json.dumps(merged["character_state"], ensure_ascii=False)[:4000]
                    if isinstance(merged.get("plot_arcs"), list):
                        project.plot_arcs = json.dumps(merged["plot_arcs"], ensure_ascii=False)[:4000]
                    if merged.get("particle_ledger"):
                        project.particle_ledger = str(merged["particle_ledger"])[:1500]
            except Exception:  # noqa: BLE001
                yield sse({"event": "merge_warn", "message": "汇总合成失败，真相文件可从分批结果人工整理"})

            # ---- 4. 角色卡 + 世界观入库（按名去重）----
            char_count = 0
            seen_names: set[str] = set()
            for note in batch_notes:
                for c in note.get("characters", []) if isinstance(note.get("characters"), list) else []:
                    if not isinstance(c, dict) or not c.get("name"):
                        continue
                    name = str(c["name"]).strip()[:100]
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    db.add(
                        Character(
                            novel_id=novel.id,
                            name=name,
                            role=str(c.get("role", "配角"))[:50],
                            description=str(c.get("brief", ""))[:500],
                        )
                    )
                    char_count += 1
            wv_count = 0
            seen_wv: set[str] = set()
            for note in batch_notes:
                for w in note.get("worldview", []) if isinstance(note.get("worldview"), list) else []:
                    if not isinstance(w, dict) or not w.get("title"):
                        continue
                    title = str(w["title"]).strip()[:200]
                    if title in seen_wv:
                        continue
                    seen_wv.add(title)
                    db.add(
                        WorldviewEntry(
                            novel_id=novel.id,
                            category=str(w.get("category", "其他"))[:50],
                            title=title,
                            content=str(w.get("content", ""))[:1000],
                        )
                    )
                    wv_count += 1
            await db.commit()
            yield sse({
                "event": "done",
                "project_id": project.id,
                "chapters": len(chapters),
                "total_words": total_words,
                "characters": char_count,
                "worldview": wv_count,
            })
        except Exception as exc:  # noqa: BLE001  顶层兜底：任何异常都转成事件，前端不悬空
            yield sse({"event": "error", "message": f"导入失败: {exc.__class__.__name__}: {exc}"})

    return StreamingResponse(generate(), media_type="text/event-stream")
