"""AI 生成服务 - 支持6大提供商 + 自定义模型"""
import httpx
import os
from services.ai_providers import get_provider, get_all_providers

SYSTEM_PROMPTS = {
    "characters": "你是一位人物设计师，擅长创造立体丰满的小说人物。请按「姓名、性别、年龄、角色定位、性格、背景、经历、外貌描写、能力、动机、人际关系、弱点、说话风格」格式输出完整14维人设卡。每个维度不少于2句话。",
    "settings": "你是一位世界观设计师，擅长构建宏大细致的奇幻/科幻/历史世界观。请根据用户选择的维度输出详细设定，不少于500字。",
    "outline": "你是一位资深网文编辑，擅长设计引人入胜的故事大纲。请生成「卷→章节→细纲」三层结构。",
    "expand": "你是一位网文作家，擅长细腻的场景描写、人物动作和氛围营造。请根据原文进行扩写，保留原有风格和关键对白。",
    "polish": "你是一位专业文字编辑，擅长润色网文语言。请优化以下段落的文笔，保留原意，增强画面感和节奏感。",
    "continue": "你是一位网文作家。请根据以下内容自然续写下一段，保持风格一致，情节连贯。",
    "chat": "你是一位专业的小说创作助手。请根据用户的需求回答问题，生成小说相关的内容。",
}


async def generate(
    provider_id: str,
    action: str,
    prompt: str,
    model: str = None,
    temperature: float = 0.8,
    max_tokens: int = 4096,
) -> dict:
    """调用AI生成内容

    Args:
        provider_id: 提供商ID (volcengine/deepseek/minimax/...)
        action: 动作 (characters/settings/outline/expand/polish/continue/chat)
        prompt: 提示词
        model: 可选，覆盖默认模型
        temperature: 温度 (0-2)
        max_tokens: 最大输出token数
    """
    provider = get_provider(provider_id)
    if not provider:
        return {"error": f"未知提供商: {provider_id}"}
    if not provider["api_key"]:
        return {"error": f"API Key 未配置 ({provider['name']})，请在环境变量中设置 {provider['api_key_env']}"}

    system_prompt = SYSTEM_PROMPTS.get(action, "你是一位专业的小说创作助手。")

    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json"
    }

    # Some providers need extra headers
    if provider_id == "minimax":
        headers["X-Api-Key"] = provider["api_key"]

    body = {
        "model": model or provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{provider['base_url']}/chat/completions",
                json=body,
                headers=headers,
            )
            if resp.status_code != 200:
                return {"error": f"API错误 {resp.status_code}: {resp.text[:300]}"}

            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return {"content": content}

    except httpx.TimeoutException:
        return {"error": "请求超时，请稍后重试"}
    except Exception as e:
        return {"error": str(e)}


def get_providers() -> list[dict]:
    """获取所有提供商列表（前端使用）"""
    return get_all_providers()
