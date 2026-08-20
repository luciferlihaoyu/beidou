"""AI 模块：接口配置管理 + OpenAI 兼容协议的流式对话/续写/大纲/审查。"""

import json

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_ai_config, get_current_user, get_owned_novel
from ..models import AIConfig, Chapter, ChatMessage, Novel, User
from ..utils import strip_html

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _normalize_base(base_url: str) -> str:
    """规范化接口地址：去掉末尾斜杠和 /v1 后缀，避免拼出 /v1/v1/... 的错误路径。"""
    base = base_url.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


# ---------- 配置管理 ----------

class AIConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="https://api.deepseek.com", max_length=300)
    api_key: str = Field(default="", max_length=300)
    model: str = Field(default="deepseek-chat", max_length=100)
    is_default: bool = False


class AIConfigOut(BaseModel):
    id: int
    name: str
    base_url: str
    model: str
    is_default: bool
    has_key: bool = False

    model_config = {"from_attributes": True}


def _to_out(config: AIConfig) -> AIConfigOut:
    out = AIConfigOut.model_validate(config)
    out.has_key = bool(config.api_key)
    return out


@router.get("/configs", response_model=list[AIConfigOut])
async def list_configs(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AIConfig).where(AIConfig.user_id == user.id).order_by(AIConfig.is_default.desc(), AIConfig.id)
    )
    return [_to_out(c) for c in result.scalars().all()]


@router.post("/configs", response_model=AIConfigOut)
async def create_config(data: AIConfigIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if data.is_default:
        await db.execute(update(AIConfig).where(AIConfig.user_id == user.id).values(is_default=False))
    config = AIConfig(user_id=user.id, **data.model_dump())
    db.add(config)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, f"已存在同名配置「{data.name}」，请换个名称或直接编辑原配置")
    await db.refresh(config)
    return _to_out(config)


@router.put("/configs/{config_id}", response_model=AIConfigOut)
async def update_config(
    config_id: int, data: AIConfigIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    config = await db.get(AIConfig, config_id)
    if config is None or config.user_id != user.id:
        raise HTTPException(404, "配置不存在")
    if data.is_default:
        await db.execute(update(AIConfig).where(AIConfig.user_id == user.id).values(is_default=False))
    for key, value in data.model_dump().items():
        # api_key 留空表示不修改
        if key == "api_key" and not value:
            continue
        setattr(config, key, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, f"已存在同名配置「{data.name}」，请换个名称")
    await db.refresh(config)
    return _to_out(config)


@router.delete("/configs/{config_id}")
async def delete_config(config_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    config = await db.get(AIConfig, config_id)
    if config is None or config.user_id != user.id:
        raise HTTPException(404, "配置不存在")
    await db.delete(config)
    await db.commit()
    return {"ok": True}


# ---------- 流式对话核心 ----------

async def _stream_openai(config: AIConfig, messages: list[dict]):
    """以 SSE 形式转发 OpenAI 兼容接口的流式响应。"""
    url = _normalize_base(config.base_url) + "/v1/chat/completions"
    payload = {"model": config.model, "messages": messages, "stream": True}
    headers = {"Authorization": f"Bearer {config.api_key}"}

    async def generate():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="ignore")[:300]
                        yield f"data: {json.dumps({'error': f'AI 接口返回 {resp.status_code}: {body}'})}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {}).get("content")
                            if delta:
                                yield f"data: {json.dumps({'content': delta}, ensure_ascii=False)}\n\n"
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except httpx.HTTPError as exc:
            yield f"data: {json.dumps({'error': f'无法连接 AI 接口: {exc.__class__.__name__}'}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


class TestIn(BaseModel):
    base_url: str
    api_key: str
    model: str


@router.post("/test")
async def test_connection(data: TestIn, user: User = Depends(get_current_user)):
    """测试接口连通性（非流式，限制输出长度）。地址带 /v1 后缀也没关系，会自动规范化。"""
    url = _normalize_base(data.base_url) + "/v1/chat/completions"
    payload = {
        "model": data.model,
        "messages": [{"role": "user", "content": "回复「连接成功」四个字即可。"}],
        "max_tokens": 32,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers={"Authorization": f"Bearer {data.api_key}"})
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接: {exc.__class__.__name__}")
    if resp.status_code == 401 or resp.status_code == 403:
        raise HTTPException(400, "API Key 无效或没有权限（401/403）")
    if resp.status_code == 404:
        raise HTTPException(400, f"接口路径不存在（404）：{url}，请检查 Base URL 是否正确")
    if resp.status_code != 200:
        raise HTTPException(400, f"接口返回 {resp.status_code}: {resp.text[:200]}")
    content = resp.json()["choices"][0]["message"]["content"]
    return {"ok": True, "reply": content}


class ModelsIn(BaseModel):
    base_url: str
    api_key: str


@router.post("/models")
async def list_models(data: ModelsIn, user: User = Depends(get_current_user)):
    """从接口方拉取可用模型列表（OpenAI 兼容 GET /v1/models）。"""
    url = _normalize_base(data.base_url) + "/v1/models"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {data.api_key}"})
    except httpx.HTTPError as exc:
        raise HTTPException(400, f"无法连接: {exc.__class__.__name__}")
    if resp.status_code in (401, 403):
        raise HTTPException(400, "API Key 无效或没有权限（401/403）")
    if resp.status_code != 200:
        raise HTTPException(400, f"接口返回 {resp.status_code}: {resp.text[:200]}")
    try:
        models = sorted(m["id"] for m in resp.json().get("data", []) if isinstance(m, dict) and "id" in m)
    except Exception:
        raise HTTPException(400, "返回内容不是 OpenAI 兼容的模型列表格式")
    return {"models": models}


@router.post("/configs/{config_id}/test")
async def test_saved_config(config_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """用已保存的 Key 测试某个配置，无需重新输入。"""
    config = await db.get(AIConfig, config_id)
    if config is None or config.user_id != user.id:
        raise HTTPException(404, "配置不存在")
    if not config.api_key:
        raise HTTPException(400, "该配置没有保存 API Key，请编辑并填写")
    return await test_connection(
        TestIn(base_url=config.base_url, api_key=config.api_key, model=config.model), user
    )


# ---------- 小说上下文组装 ----------

async def _novel_context(novel: Novel, db: AsyncSession, chapter_id: int | None = None) -> str:
    parts = [f"作品：《{novel.title}》"]
    if novel.author:
        parts.append(f"作者：{novel.author}")
    if novel.genre:
        parts.append(f"类型：{novel.genre}")
    if novel.description:
        parts.append(f"简介：{novel.description}")

    from ..models import Character, Foreshadowing

    chars = (
        await db.execute(select(Character).where(Character.novel_id == novel.id).limit(20))
    ).scalars().all()
    if chars:
        parts.append("主要角色：" + "；".join(f"{c.name}（{c.role}）：{c.description[:100]}" for c in chars))

    fss = (
        await db.execute(
            select(Foreshadowing).where(Foreshadowing.novel_id == novel.id, Foreshadowing.status != "已回收").limit(10)
        )
    ).scalars().all()
    if fss:
        parts.append("未回收伏笔：" + "；".join(f.title for f in fss))

    if chapter_id:
        chapter = await db.get(Chapter, chapter_id)
        if chapter is not None and chapter.novel_id == novel.id:
            from ..models import Volume
            from ..utils import chapter_display_title, order_chapters

            chapters = (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
            volumes = (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
            ordered = order_chapters(chapters, volumes)
            number = next((i + 1 for i, c in enumerate(ordered) if c.id == chapter.id), 1)
            title = chapter_display_title(chapter.title, number)
            text = strip_html(chapter.content)
            parts.append(f"当前章节《{title}》正文（节选结尾）：\n{text[-3000:]}")
    return "\n".join(parts)


SYSTEM_PROMPT = (
    "你是一位资深网文创作助手，熟悉中文网文的节奏、爽点与读者期待。"
    "回答使用简体中文，直接给出可用的创作内容或具体建议，避免空泛客套。"
)


class ChatIn(BaseModel):
    novel_id: int
    message: str = Field(min_length=1, max_length=4000)
    chapter_id: int | None = None
    config_id: int | None = None


@router.post("/chat")
async def chat(data: ChatIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    novel = await get_owned_novel(data.novel_id, user, db)
    config = await get_ai_config(user, db, data.config_id)

    history = (
        await db.execute(
            select(ChatMessage).where(ChatMessage.novel_id == novel.id).order_by(ChatMessage.id.desc()).limit(10)
        )
    ).scalars().all()
    history.reverse()

    context = await _novel_context(novel, db, data.chapter_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT + "\n\n" + context}]
    messages += [{"role": m.role, "content": m.content} for m in history]
    messages.append({"role": "user", "content": data.message})

    db.add(ChatMessage(novel_id=novel.id, role="user", content=data.message))
    await db.commit()
    return await _stream_openai(config, messages)


class QuickActionIn(BaseModel):
    novel_id: int
    chapter_id: int | None = None
    instruction: str = Field(default="", max_length=1000)
    config_id: int | None = None


ACTION_PROMPTS = {
    "continue": "请基于以上设定与当前章节内容，直接续写约 800 字的正文。只输出正文，不要解释。",
    "outline": "请基于以上设定，为后续剧情设计 5 个章节的大纲建议，每章一行，格式「章节名：剧情要点」。",
    "review": "请审查当前章节内容，指出其中可能存在的逻辑矛盾、设定冲突或与未回收伏笔的脱节，按条列出并给出修改建议。",
}


@router.post("/action/{action}")
async def quick_action(
    action: str, data: QuickActionIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    if action not in ACTION_PROMPTS:
        raise HTTPException(404, "不支持的操作")
    novel = await get_owned_novel(data.novel_id, user, db)
    config = await get_ai_config(user, db, data.config_id)
    context = await _novel_context(novel, db, data.chapter_id)
    prompt = ACTION_PROMPTS[action]
    if data.instruction:
        prompt += f"\n补充要求：{data.instruction}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": context + "\n\n" + prompt},
    ]
    return await _stream_openai(config, messages)
