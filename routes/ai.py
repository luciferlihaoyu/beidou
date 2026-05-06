"""AI 生成 API 路由"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from services.ai_service import generate, get_providers

router = APIRouter(prefix="/api/ai", tags=["ai"])


class GenerateRequest(BaseModel):
    provider: str = "volcengine"
    action: str = "expand"  # characters/settings/outline/expand/polish/continue/chat
    prompt: str = ""
    model: Optional[str] = None  # 可选，覆盖默认模型
    temperature: Optional[float] = 0.8
    max_tokens: Optional[int] = 4096


@router.get("/providers")
def list_providers():
    return get_providers()


@router.post("/generate")
async def ai_generate(req: GenerateRequest):
    result = await generate(
        provider_id=req.provider,
        action=req.action,
        prompt=req.prompt,
        model=req.model,
        temperature=req.temperature or 0.8,
        max_tokens=req.max_tokens or 4096,
    )
    return result
