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
