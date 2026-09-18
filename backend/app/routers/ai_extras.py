"""AI 工厂 M5：多版本简介 / 市场雷达（选题环节）/ 封面 prompt + 天宫任务通道。

借鉴 webnovel-master 的 webnovel-synopsis / webnovel-radar / webnovel-cover
（美智子作品）改编：
- 简介：4 版本（精简投稿/标准/推广/抖音文案），立项确认后即可生成
- 市场雷达：选题环节市场调研（题材热度/流行元素/钩子/爽点/读者偏好/更新建议），
  报告自动注入立项 prompt 提升选题质量
- 封面：LLM 生成中文画面描述 + 英文绘图 prompt；配置了天宫通道
  （env TIANGONG_BASE_URL + TIANGONG_SERVICE_KEY）时自动创建天宫任务，
  未配置则交付可复制 prompt（贴给碧霄/婉儿即可发任务）
"""

from __future__ import annotations

import json
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_current_user
from ..models import AiProject, User
from ..utils import chapter_display_title, strip_html
from .ai_factory import _SYSTEM, _chat_text, _get_project, _parse_json, _pick_config

router = APIRouter(prefix="/api/ai-factory", tags=["ai-factory"])


def _save_json(p: AiProject, field: str, value: dict, db_commit=None) -> None:
    setattr(p, field, json.dumps(value, ensure_ascii=False))


@router.post("/projects/{project_id}/synopsis")
async def gen_synopsis(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """生成多版本简介（webnovel-synopsis 改编：4 版本适配不同投放场景）。"""
    p = await _get_project(project_id, user, db)
    spec = json.loads(p.book_spec_json or "{}")
    if not spec and p.status == "draft":
        raise HTTPException(400, "请先生成立项草案（或确认立项）再生成简介")
    title = (spec.get("titles") or [p.seed_prompt[:20]])[0]
    prompt = (
        f"网文《{title}》，类型：{p.genre or spec.get('genre', '不限')}。"
        f"核心梗概：{spec.get('premise', p.seed_prompt)}\n\n"
        "请生成 4 个版本的小说简介，只输出 JSON：\n"
        "{\n"
        '  "short": "精简版（50-100字，平台投稿用）",\n'
        '  "standard": "标准版（200-300字，详情页简介）",\n'
        '  "promotion": "推广版（500字内，社区推文用，含看点列举）",\n'
        '  "douyin": "抖音版（30字内，短视频文案钩子）"\n'
        "}\n要求：每版都要有钩子感；不得剧透结局；抖音版要口语化有冲突。"
    )
    config = await _pick_config(user, db, p.setup_llm)
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=2500)
    data = _parse_json(raw)
    if not isinstance(data, dict) or not any(data.get(k) for k in ("short", "standard", "promotion", "douyin")):
        raise HTTPException(400, "AI 未返回可解析的简介，请重试")
    result = {k: str(data.get(k, ""))[:800] for k in ("short", "standard", "promotion", "douyin")}
    _save_json(p, "synopsis_json", result)
    await db.commit()
    return result


@router.post("/projects/{project_id}/market-scan")
async def market_scan(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """市场雷达（webnovel-radar 改编）：选题环节的市场调研，报告注入立项 prompt。

    调研维度：题材热度/流行元素/钩子类型/爽点偏好/读者画像/更新建议。
    """
    p = await _get_project(project_id, user, db)
    prompt = (
        f"你是网文市场分析师。针对以下选题做市场调研：\n"
        f"创意：{p.seed_prompt}\n类型：{p.genre or '（未指定，请自行判断最匹配的类型）'}\n\n"
        "基于你对中文网文市场（起点/番茄/七猫等平台）的了解，输出 JSON：\n"
        "{\n"
        '  "genre_heat": "该题材当前热度评估（含趋势判断，≤100字）",\n'
        '  "trending_elements": ["当前该题材最受欢迎的 3-5 个流行元素/套路"],\n'
        '  "hot_hooks": ["该题材最有效的开篇钩子类型 2-3 个"],\n'
        '  "cool_point_trends": ["该题材读者当前最买账的爽点类型 2-3 个"],\n'
        '  "reader_profile": "目标读者画像一句话",\n'
        '  "update_advice": "更新节奏与字数建议一句话",\n'
        '  "differentiation": "本书创意可打的差异化点 1-2 个",\n'
        '  "verdict": "选题总评：值得做/可做但需调整/不建议 + 一句话理由"\n'
        "}\n要求：具体、可执行，不要空话套话。"
    )
    config = await _pick_config(user, db, p.setup_llm)
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=2000)
    data = _parse_json(raw)
    if not isinstance(data, dict) or not data.get("verdict"):
        raise HTTPException(400, "AI 未返回可解析的调研报告，请重试")
    _save_json(p, "market_json", data)
    await db.commit()
    return data


class CoverPromptIn(BaseModel):
    style: str = Field(default="玄幻风插画", max_length=50)


@router.post("/projects/{project_id}/cover-prompt")
async def cover_prompt(
    project_id: int,
    data: CoverPromptIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成封面绘图 prompt；配置了天宫通道时同时创建天宫封面任务。

    天宫通道（env 配置即启用）：
    - TIANGONG_BASE_URL：天宫平台地址
    - TIANGONG_SERVICE_KEY：北斗服务密钥（天宫侧 issueServiceKey 签发）
    通道未配置 → 返回可复制 prompt（交给碧霄/婉儿在天宫发任务即可）。
    """
    p = await _get_project(project_id, user, db)
    spec = json.loads(p.book_spec_json or "{}")
    title = (spec.get("titles") or [p.seed_prompt[:20]])[0]
    synopsis = json.loads(p.synopsis_json or "{}").get("short", "") or spec.get("premise", p.seed_prompt)

    prompt = (
        f"为网文《{title}》（{p.genre or '通用'}）设计封面。\n"
        f"简介：{synopsis}\n画风要求：{data.style}\n\n"
        "只输出 JSON：\n"
        "{\n"
        '  "concept": "中文画面描述（构图/主体/色彩/氛围，≤120字）",\n'
        '  "prompt_en": "英文绘图 prompt（适用于 gpt-image/seedream 等图像模型，含风格、构图、光线、质量词，≤150 词，不要出现文字元素）",\n'
        '  "negative": "负面 prompt（避免出现的内容，≤40 词）"\n'
        "}"
    )
    config = await _pick_config(user, db, p.setup_llm)
    raw = await _chat_text(config, _SYSTEM, prompt, max_tokens=1200)
    result = _parse_json(raw)
    if not isinstance(result, dict) or not result.get("prompt_en"):
        raise HTTPException(400, "AI 未返回可解析的封面方案，请重试")
    _save_json(p, "cover_prompt", result)

    # ---- 天宫任务通道（可选启用）----
    task_created: dict | None = None
    tg_base = os.environ.get("TIANGONG_BASE_URL", "").rstrip("/")
    tg_key = os.environ.get("TIANGONG_SERVICE_KEY", "")
    tg_key_id = os.environ.get("TIANGONG_SERVICE_KEY_ID", "")
    if tg_base and tg_key and tg_key_id:
        try:
            import hashlib
            import time

            external_ref = f"beidou-cover-{p.id}-{int(time.time())}"
            body = {
                "external_ref": external_ref,
                "idempotency_key": hashlib.sha256(external_ref.encode()).hexdigest()[:32],
                "operation": "create",
                "target": "cover-generate",
                "params_snapshot": {
                    "title": title,
                    "genre": p.genre,
                    "prompt_en": result.get("prompt_en", ""),
                    "negative": result.get("negative", ""),
                },
                "origin_system": "beidou",
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
                resp = await client.post(
                    f"{tg_base}/trpc/beidouExternal.create",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {tg_key}",
                        "X-TG-Service-Key-ID": tg_key_id,
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code < 300:
                task_created = {"ref": external_ref, "status": "submitted"}
        except httpx.HTTPError:
            pass  # 通道失败不影响 prompt 交付

    await db.commit()
    return {**result, "tiangong_task": task_created}


@router.post("/projects/{project_id}/kb-sync")
async def kb_sync(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """把项目设定打包上传璇玑知识库（幂等 upsert，同标题覆盖）。

    打包内容：立项 book_spec + 角色卡 + 世界观条目 + 状态文件摘要。
    上传后璇玑侧可被检索——配合项目 kb_query 形成「设定入知识库 → 生成时召回」闭环。
    """
    from sqlalchemy import select as _select

    from ..models import Character, WorldviewEntry
    from .integrations import _xuanji_mcp

    p = await _get_project(project_id, user, db)
    spec = json.loads(p.book_spec_json or "{}")
    title = (spec.get("titles") or [p.seed_prompt[:20]])[0]

    parts = [f"# 《{title}》设定集（北斗 AI 工厂同步）\n"]
    if spec:
        parts.append("## 立项\n" + json.dumps(spec, ensure_ascii=False, indent=2))
    if p.author_intent:
        parts.append(f"## 作者意图\n{p.author_intent}")
    if p.global_summary:
        parts.append(f"## 剧情摘要\n{p.global_summary}")
    if p.character_state:
        parts.append(f"## 角色状态\n{p.character_state}")
    if p.novel_id:
        chars = (
            (await db.execute(_select(Character).where(Character.novel_id == p.novel_id)))
            .scalars()
            .all()
        )
        if chars:
            parts.append("## 角色卡\n" + "\n".join(f"- **{c.name}**（{c.role}）：{(c.description or '')[:200]}" for c in chars))
        wvs = (
            (await db.execute(_select(WorldviewEntry).where(WorldviewEntry.novel_id == p.novel_id)))
            .scalars()
            .all()
        )
        if wvs:
            parts.append("## 世界观\n" + "\n".join(f"- [{w.category}] {w.title}：{(w.content or '')[:200]}" for w in wvs))
    content = "\n\n".join(parts)

    from ..models import IntegrationConfig as IC

    kc = (
        (await db.execute(_select(IC).where(IC.user_id == user.id)))
        .scalars()
        .first()
    )
    if not kc or not kc.xuanji_url:
        raise HTTPException(400, "请先在「账号设置 → 集成」里配置璇玑地址和 API Key")

    from .integrations import _xuanji_ensure_folder

    folder_id = await _xuanji_ensure_folder(kc, "北斗小说资料")
    args: dict = {"title": f"北斗·{title}·设定集", "content": content}
    if folder_id is not None:
        args["folderId"] = folder_id
    result = await _xuanji_mcp(kc, "document_upsert", args)
    return {"ok": True, "title": f"北斗·{title}·设定集", "chars": len(content), "result": result}


# ================= 投稿导出包 =================

def _safe_name(name: str) -> str:
    """清理文件/目录名里的路径危险字符。"""
    import re as _re

    return _re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "未命名"


def _build_export_pack(p: AiProject, novel, groups) -> bytes:
    """生成投稿包 zip（纯本地计算）：

    - 正文/[第N卷_卷名/]第001章_标题.txt（纯文本，按章节顺序）
    - 敏感词终检报告.txt（仅 done 章参与 lint，只列有问题的章节；平均分 ≥90 判可投稿）
    - 简介.txt / 封面prompt.txt / 书籍信息.json（有则给）
    """
    import io
    import zipfile
    from datetime import datetime

    from .. import censor
    from ..textlint import PLATFORM_PROFILES, lint

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # ---- 1. 正文 ----
        vol_index = 0
        chapter_total = 0
        word_total = 0
        done_reports: list[dict] = []  # {number, title, score, issues}
        done_count = 0
        custom_words = [w.strip() for w in (p.custom_words or "").split(",") if w.strip()]
        for volume, chapters in groups:
            subdir = ""
            if volume is not None:
                vol_index += 1
                subdir = f"正文/第{vol_index}卷_{_safe_name(volume.title)}/"
            else:
                subdir = "正文/"
            for chapter, number in chapters:
                text = strip_html(chapter.content).strip()
                if not text:
                    continue
                chapter_total += 1
                word_total += chapter.word_count or len(text)
                display = chapter_display_title(chapter.title, number)
                fname = f"{subdir}第{number:03d}章_{_safe_name(chapter.title.strip() or display)}.txt"
                z.writestr(fname, f"{display}\n\n{text}\n")
                # 2. 敏感词终检（只跑 done 章）
                if chapter.status == "done":
                    done_count += 1
                    rep = lint(text, platform=p.platform or "", custom_words=custom_words)
                    done_reports.append(
                        {"number": number, "title": display, "score": rep["score"], "issues": rep["issues"]}
                    )

        # ---- 2. 敏感词终检报告 ----
        platform_name = PLATFORM_PROFILES.get(p.platform or "", PLATFORM_PROFILES[""])["name"]
        lines = [
            f"《{novel.title}》敏感词终检报告",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"目标平台：{platform_name}" + (f"（自定义词 {len(custom_words)} 个）" if custom_words else ""),
            f"检测范围：done 章节 {done_count} 章 / 全书共 {chapter_total} 章",
            "",
            "分级说明：🔴 硬红线（必改）｜🟠 影响审核或推荐｜🔵 待结合语境复核",
            "",
        ]
        problem = [r for r in done_reports if r["issues"]]
        all_issues = [i for r in done_reports for i in r["issues"]]
        block_count = sum(1 for i in all_issues if i.get("level") == "block")
        if not done_reports:
            lines.append("（没有已完成章节可检测）")
        elif not problem:
            lines.append("✅ 全部检测章节未命中敏感词/规范问题。")
        else:
            for r in problem:
                lines.append(f"—— {r['title']}（规范得分 {r['score']}）——")
                lines.extend(censor.format_issues(r["issues"]))
            lines.extend(censor.summarize(all_issues))
            lines.append("")
        if done_reports:
            avg = round(sum(r["score"] for r in done_reports) / len(done_reports), 1)
            lines.append("———————")
            lines.append(f"全书平均分：{avg}")
            lines.append("结论：" + censor.verdict(avg, block_count))
        lines.append("")
        lines.append("说明：" + censor.SUMMARY)
        lines.append(
            "本报告按公开审核口径做本地排查，不是任何平台的确切词库；"
            "标记为「待复核」的项需结合剧情语境判断，不要机械替换。"
        )
        z.writestr("敏感词终检报告.txt", "\n".join(lines) + "\n")

        # ---- 3. 简介（4 版本，如有）----
        synopsis = json.loads(p.synopsis_json) if p.synopsis_json else {}
        if any(synopsis.get(k) for k in ("short", "standard", "promotion", "douyin")):
            label_map = [("short", "精简版（投稿用）"), ("standard", "标准版（详情页）"),
                         ("promotion", "推广版（社区推文）"), ("douyin", "抖音版（短视频钩子）")]
            parts = [f"《{novel.title}》多版本简介\n"]
            for k, lab in label_map:
                if synopsis.get(k):
                    parts.append(f"【{lab}】\n{synopsis[k]}\n")
            z.writestr("简介.txt", "\n".join(parts))

        # ---- 4. 封面 prompt（如有）----
        cover = json.loads(p.cover_prompt) if p.cover_prompt else {}
        if cover.get("concept") or cover.get("prompt_en"):
            parts = [f"《{novel.title}》封面设计\n"]
            if cover.get("concept"):
                parts.append(f"【中文构思】\n{cover['concept']}\n")
            if cover.get("prompt_en"):
                parts.append(f"【英文 Prompt】\n{cover['prompt_en']}\n")
            if cover.get("negative"):
                parts.append(f"【负面 Prompt】\n{cover['negative']}\n")
            z.writestr("封面prompt.txt", "\n".join(parts))

        # ---- 5. 书籍信息 ----
        spec = json.loads(p.book_spec_json) if p.book_spec_json else {}
        info = {
            "书名": novel.title,
            "作者": novel.author or "",
            "题材": p.genre or novel.genre or "",
            "状态": novel.status,
            "目标平台": platform_name,
            "章数": chapter_total,
            "总字数": word_total,
            "导出时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "核心梗概": spec.get("premise", ""),
            "来源": "北斗 AI 工厂投稿包",
        }
        z.writestr("书籍信息.json", json.dumps(info, ensure_ascii=False, indent=2))

    return buf.getvalue()


@router.get("/projects/{project_id}/export-pack")
async def export_pack(project_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """投稿导出包：分章 TXT + 敏感词终检报告 + 简介 + 封面 prompt + 书籍信息，zip 一键下载。"""
    import io
    from datetime import datetime
    from urllib.parse import quote

    from sqlalchemy import select as _select
    from fastapi.responses import StreamingResponse

    from ..models import Chapter, Novel, Volume
    from ..utils import order_chapters as _order

    p = await _get_project(project_id, user, db)
    if p.novel_id is None:
        raise HTTPException(400, "项目未关联小说")
    novel = await db.get(Novel, p.novel_id)
    if novel is None:
        raise HTTPException(404, "关联小说不存在")

    chapters = (await db.execute(_select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    volumes = (await db.execute(_select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    ordered = _order(chapters, volumes)
    volume_map = {v.id: v for v in volumes}
    # 分组：与 export.py 相同的卷组织逻辑（跳过完全空章）
    groups: list = []
    for i, chapter in enumerate(ordered):
        if not strip_html(chapter.content).strip():
            continue
        volume = volume_map.get(chapter.volume_id)
        if not groups or groups[-1][0] != volume:
            groups.append((volume, []))
        groups[-1][1].append((chapter, i + 1))
    if not groups:
        raise HTTPException(400, "还没有可导出的章节内容")

    data = _build_export_pack(p, novel, groups)
    filename = f"投稿包_{_safe_name(novel.title)}_{datetime.now().strftime('%Y%m%d')}.zip"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(io.BytesIO(data), media_type="application/zip", headers=headers)
