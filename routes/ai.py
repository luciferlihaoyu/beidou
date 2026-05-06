"""AI generation API route"""
from fastapi import APIRouter
from pydantic import BaseModel
from services.ai_service import generate, get_providers

router = APIRouter(prefix="/api/ai", tags=["ai"])


class GenerateRequest(BaseModel):
    provider: str = "volcengine"
    action: str = "expand"  # characters/settings/outline/expand/polish/continue
    prompt: str = ""
    book_title: str = ""


@router.get("/providers")
def list_providers():
    return get_providers()


@router.post("/generate")
async def ai_generate(req: GenerateRequest):
    result = await generate(req.provider, req.action, req.prompt)
    return result
