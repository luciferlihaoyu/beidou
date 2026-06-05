"""AI integration — WebSocket streaming chat + HTTP endpoints for continue/outline/review."""

import json
import logging
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_approved_user
from app.db.session import get_db, async_session_factory
from app.models.agent import Agent
from app.models.chapter import Chapter
from app.models.model_config import ModelConfig
from app.models.novel import Novel
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# WebSocket streaming endpoint
# ---------------------------------------------------------------------------


@router.websocket("/api/ai/chat")
async def ai_chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for streaming AI chat.

    Client connects with ?token=<JWT> query parameter for authentication.
    Client sends: {"type": "chat", "agent_id": 1, "content": "...", "messages": [...]}
    Server streams back: {"type": "chat_response", "content": "...", "done": false/true}
    """
    # Verify JWT token before accepting
    from jose import JWTError, jwt
    from app.core.config import get_settings

    app_settings = get_settings()
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        payload = jwt.decode(token, app_settings.JWT_SECRET_KEY, algorithms=[app_settings.JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            await websocket.close(code=4001, reason="Invalid token")
            return
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token")
        return

    # Verify user exists and is approved
    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            await websocket.close(code=4001, reason="User not found")
            return
        if user.role != "admin" and user.status != "approved":
            await websocket.close(code=4003, reason="Account pending approval")
            return

    await websocket.accept()
    logger.info("AI chat WebSocket connected (user %d)", user_id)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "content": "Invalid JSON"})
                continue

            msg_type = message.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type != "chat":
                continue

            content = message.get("content", "")
            agent_id = message.get("agent_id")
            history_messages = message.get("messages", [])

            # Load agent and model config from DB
            async with async_session_factory() as db:
                try:
                    system_prompt = ""
                    api_base: Optional[str] = None
                    api_key: Optional[str] = None
                    model_id = "gpt-3.5-turbo"

                    if agent_id:
                        result = await db.execute(select(Agent).where(Agent.id == agent_id))
                        agent = result.scalar_one_or_none()
                        if agent:
                            system_prompt = agent.system_prompt or ""
                            if agent.model_config_id:
                                mc_result = await db.execute(
                                    select(ModelConfig).where(ModelConfig.id == agent.model_config_id)
                                )
                                mc = mc_result.scalar_one_or_none()
                                if mc:
                                    api_base = mc.api_base
                                    api_key = mc.api_key
                                    model_id = mc.model_id

                    # Build messages list
                    api_messages: list = []
                    if system_prompt:
                        api_messages.append({"role": "system", "content": system_prompt})

                    # Include recent history (last 20 messages to avoid context overflow)
                    for h in history_messages[-20:]:
                        role = h.get("role", "user")
                        c = h.get("content", "")
                        if role in ("user", "assistant", "system"):
                            api_messages.append({"role": role, "content": c})

                    # Append current user message
                    api_messages.append({"role": "user", "content": content})

                    # Call AI model with streaming
                    try:
                        async with httpx.AsyncClient(timeout=120.0) as client:
                            req_url = (api_base or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
                            headers = {"Content-Type": "application/json"}
                            if api_key:
                                headers["Authorization"] = f"Bearer {api_key}"

                            async with client.stream(
                                "POST",
                                req_url,
                                json={
                                    "model": model_id,
                                    "messages": api_messages,
                                    "stream": True,
                                },
                                headers=headers,
                            ) as response:
                                if response.status_code != 200:
                                    error_text = await response.aread()
                                    await websocket.send_json({
                                        "type": "error",
                                        "content": f"AI API error ({response.status_code}): {error_text.decode()[:200]}",
                                    })
                                    continue

                                async for line in response.aiter_lines():
                                    if not line.startswith("data: "):
                                        continue
                                    data_str = line[6:]
                                    if data_str.strip() == "[DONE]":
                                        break
                                    try:
                                        chunk = json.loads(data_str)
                                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                                        text = delta.get("content", "")
                                        if text:
                                            await websocket.send_json({
                                                "type": "chat_response",
                                                "content": text,
                                                "done": False,
                                            })
                                    except json.JSONDecodeError:
                                        continue

                                # Final done message
                                await websocket.send_json({
                                    "type": "chat_response",
                                    "content": "",
                                    "done": True,
                                })

                    except httpx.RequestError as exc:
                        logger.error("AI API request failed: %s", exc)
                        await websocket.send_json({
                            "type": "error",
                            "content": f"AI service unavailable: {exc}",
                        })

                except Exception as exc:
                    logger.exception("Chat processing error")
                    await websocket.send_json({
                        "type": "error",
                        "content": f"处理消息时出错: {exc}",
                    })

    except WebSocketDisconnect:
        logger.info("AI chat WebSocket disconnected")


# ---------------------------------------------------------------------------
# HTTP: non-streaming AI chat
# ---------------------------------------------------------------------------


class ChatHttpRequest(BaseModel):
    novel_id: Optional[int] = None
    agent_id: Optional[int] = None
    messages: List[dict]


@router.post("/api/ai/chat-http")
async def ai_chat_http(
    body: ChatHttpRequest,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Non-streaming AI chat endpoint."""
    content = await _call_ai(body.agent_id, body.messages, db)
    return {"response": content}


# ---------------------------------------------------------------------------
# AI: continue writing
# ---------------------------------------------------------------------------


class ContinueRequest(BaseModel):
    novel_id: int
    chapter_id: int
    agent_id: Optional[int] = None


@router.post("/api/ai/continue")
async def ai_continue(
    body: ContinueRequest,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a writing continuation for a chapter."""
    # Fetch chapter
    result = await db.execute(
        select(Chapter).where(Chapter.id == body.chapter_id, Chapter.novel_id == body.novel_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter_content = chapter.content or ""
    # Take last ~500 chars as context
    context = chapter_content[-500:] if len(chapter_content) > 500 else chapter_content

    prompt_messages = [
        {
            "role": "user",
            "content": (
                f"请根据以下章节内容进行续写，延续文风和情节，"
                f"直接输出续写内容，不需要额外说明：\n\n"
                f"章节标题：{chapter.title}\n"
                f"最近内容：{context}\n\n续写："
            ),
        }
    ]

    content = await _call_ai(body.agent_id, prompt_messages, db)
    return {"response": content}


# ---------------------------------------------------------------------------
# AI: outline generation
# ---------------------------------------------------------------------------


class OutlineRequest(BaseModel):
    novel_id: int
    agent_id: Optional[int] = None


@router.post("/api/ai/outline")
async def ai_outline(
    body: OutlineRequest,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate an outline suggestion based on novel settings and chapters."""
    # Fetch novel
    result = await db.execute(select(Novel).where(Novel.id == body.novel_id))
    novel = result.scalar_one_or_none()
    if not novel:
        raise HTTPException(status_code=404, detail="Novel not found")

    # Fetch chapters
    ch_result = await db.execute(
        select(Chapter).where(Chapter.novel_id == body.novel_id).order_by(Chapter.order_index)
    )
    chapters = ch_result.scalars().all()

    chapter_summaries = "\n".join(
        f"- 第{i+1}章《{ch.title}》（{ch.word_count}字）"
        for i, ch in enumerate(chapters)
    )

    prompt_messages = [
        {
            "role": "user",
            "content": (
                f"请为以下小说生成大纲建议，分析现有章节结构并提出优化建议：\n\n"
                f"小说标题：{novel.title}\n"
                f"简介：{novel.synopsis or '无'}\n"
                f"类型：{novel.genre or '未设定'}\n"
                f"标签：{novel.tags or '无'}\n\n"
                f"现有章节：\n{chapter_summaries if chapter_summaries else '暂无章节'}\n\n"
                f"请从以下方面给出建议：\n"
                f"1. 整体结构分析\n"
                f"2. 章节编排优化\n"
                f"3. 情节推进建议\n"
                f"4. 后续章节规划"
            ),
        }
    ]

    content = await _call_ai(body.agent_id, prompt_messages, db)
    return {"response": content}


# ---------------------------------------------------------------------------
# AI: review
# ---------------------------------------------------------------------------


class ReviewRequest(BaseModel):
    novel_id: int
    chapter_id: int
    agent_id: Optional[int] = None


@router.post("/api/ai/review")
async def ai_review(
    body: ReviewRequest,
    user: User = Depends(get_current_approved_user),
    db: AsyncSession = Depends(get_db),
):
    """Review a chapter for logic and consistency issues."""
    # Fetch chapter
    result = await db.execute(
        select(Chapter).where(Chapter.id == body.chapter_id, Chapter.novel_id == body.novel_id)
    )
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Fetch novel info for context
    novel_result = await db.execute(select(Novel).where(Novel.id == body.novel_id))
    novel = novel_result.scalar_one_or_none()

    # Get other chapters for cross-reference
    ch_result = await db.execute(
        select(Chapter)
        .where(Chapter.novel_id == body.novel_id, Chapter.id != body.chapter_id)
        .order_by(Chapter.order_index)
    )
    other_chapters = ch_result.scalars().all()

    other_titles = "\n".join(f"- {ch.title}" for ch in other_chapters) if other_chapters else "无"

    prompt_messages = [
        {
            "role": "user",
            "content": (
                f"请审查以下章节，检查逻辑矛盾和设定冲突：\n\n"
                f"小说：{novel.title if novel else '未知'}\n"
                f"类型：{novel.genre if novel else '未设定'}\n\n"
                f"当前章节：第{chapter.order_index + 1}章《{chapter.title}》\n"
                f"内容：\n{chapter.content or '(空)'}\n\n"
                f"其他章节标题：\n{other_titles}\n\n"
                f"请从以下方面审查：\n"
                f"1. 逻辑矛盾\n"
                f"2. 设定冲突\n"
                f"3. 人物行为一致性\n"
                f"4. 时间线问题"
            ),
        }
    ]

    content = await _call_ai(body.agent_id, prompt_messages, db)
    return {"response": content}


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


async def _call_ai(
    agent_id: Optional[int],
    messages: List[dict],
    db: AsyncSession,
) -> str:
    """Call the AI model (non-streaming) using agent and model config from DB.
    Returns the raw text content of the AI response."""
    system_prompt = ""
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    model_id = "gpt-3.5-turbo"

    if agent_id:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if agent:
            system_prompt = agent.system_prompt or ""
            if agent.model_config_id:
                mc_result = await db.execute(
                    select(ModelConfig).where(ModelConfig.id == agent.model_config_id)
                )
                mc = mc_result.scalar_one_or_none()
                if mc:
                    api_base = mc.api_base
                    api_key = mc.api_key
                    model_id = mc.model_id

    # Build messages
    api_messages: list = []
    if system_prompt:
        api_messages.append({"role": "system", "content": system_prompt})
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role in ("user", "assistant", "system"):
            api_messages.append({"role": role, "content": content})

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            req_url = (api_base or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            resp = await client.post(
                req_url,
                json={
                    "model": model_id,
                    "messages": api_messages,
                    "stream": False,
                },
                headers=headers,
            )

            if resp.status_code != 200:
                error_detail = resp.text[:300]
                logger.error("AI API error (%d): %s", resp.status_code, error_detail)
                raise HTTPException(
                    status_code=502,
                    detail=f"AI API error: {error_detail}",
                )

            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content

    except httpx.RequestError as exc:
        logger.error("AI API request failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"AI service unavailable: {exc}")
