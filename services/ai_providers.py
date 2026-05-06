"""墨韵 AI 提供商配置
================================
用户自定义说明：
1. 在此文件中修改 PROVIDERS 字典即可增删改模型
2. 每个提供商需要: base_url (API地址), model (模型名), api_key (环境变量名)
3. 环境变量在 Zeabur/服务器中设置，不写在代码里
4. 格式与 OpenAI API 兼容 (POST /chat/completions)

===== 当前提供商标配 =====
1. 火山引擎 (Doubao)  — VOLCANO_API_KEY
2. DeepSeek           — DEEPSEEK_API_KEY
3. MiniMax            — MINIMAX_API_KEY
4. 月之暗面 (Kimi)    — MOONSHOT_API_KEY
5. 阿里云百炼 (Qwen)  — BAILIAN_API_KEY
6. 小米 (MiMo)        — XIAOMI_API_KEY

===== 用户添加自定义提供商 =====
在 PROVIDERS 字典底部加一条：
    "my-model": {
        "name": "我的模型",          # 显示名
        "base_url": "https://...",  # API 地址
        "model": "model-name",     # 模型名
        "api_key_env": "MY_KEY",   # 环境变量名
    }
"""

import os

PROVIDERS = {
    # -------------------------------------------------------
    # 1. 火山引擎 (Doubao / 豆包)
    #    申请：https://console.volcengine.com/ark
    #    最新模型：doubao-seed-2-0-pro (2026-04发布)
    # -------------------------------------------------------
    "volcengine": {
        "name": "🌋 火山引擎 Doubao",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-pro",
        "api_key_env": "VOLCANO_API_KEY",
        "models": ["doubao-seed-2-0-pro", "doubao-seed-1-6-251015", "doubao-pro-256k", "doubao-lite-128k"],
    },

    # -------------------------------------------------------
    # 2. DeepSeek
    #    申请：https://platform.deepseek.com
    #    最新模型：deepseek-chat (V3), deepseek-reasoner (R1)
    # -------------------------------------------------------
    "deepseek": {
        "name": "🤖 DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },

    # -------------------------------------------------------
    # 3. MiniMax
    #    申请：https://platform.minimaxi.com
    #    最新模型：minimax-m2.7 (2026-03发布)
    #    API地址：中国大陆 api.minimaxi.com / 全球 api.minimax.io
    # -------------------------------------------------------
    "minimax": {
        "name": "💠 MiniMax M2.7",
        "base_url": "https://api.minimax.chat/v1",
        "model": "minimax-m2.7",
        "api_key_env": "MINIMAX_API_KEY",
        "models": ["minimax-m2.7", "minimax-m2.5", "abab6.5s"],
    },

    # -------------------------------------------------------
    # 4. 月之暗面 (Kimi / Moonshot)
    #    申请：https://platform.moonshot.cn
    #    最新模型：kimi-k2.5 (2026-01发布)
    # -------------------------------------------------------
    "moonshot": {
        "name": "🌙 Kimi K2.5",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k2.5",
        "api_key_env": "MOONSHOT_API_KEY",
        "models": ["kimi-k2.5", "moonshot-v1-128k", "moonshot-v1-32k"],
    },

    # -------------------------------------------------------
    # 5. 阿里云百炼 (Qwen / 通义千问)
    #    申请：https://bailian.console.aliyun.com
    #    最新模型：qwen3.5-plus (2026-02发布)
    #    DashScope API：https://dashscope.aliyuncs.com/compatible-mode/v1
    # -------------------------------------------------------
    "bailian": {
        "name": "💎 阿里云百炼 Qwen3.5",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen3.5-plus",
        "api_key_env": "BAILIAN_API_KEY",
        "models": ["qwen3.5-plus", "qwen3.5-flash", "qwen3-plus", "qwen3-flash"],
    },

    # -------------------------------------------------------
    # 6. 小米 (MiMo)
    #    申请：https://api.xiaomimimo.com
    #    最新模型：mimo-v2.5-pro (2026-04开源)
    # -------------------------------------------------------
    "xiaomi": {
        "name": "📱 小米 MiMo V2.5",
        "base_url": "https://api.xiaomimimo.com/v1",
        "model": "mimo-v2.5-pro",
        "api_key_env": "XIAOMI_API_KEY",
        "models": ["mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-flash"],
    },
}


def get_provider(provider_id: str) -> dict | None:
    """获取提供商配置"""
    p = PROVIDERS.get(provider_id)
    if not p:
        return None
    # Return a copy with resolved api_key
    return {
        **p,
        "api_key": os.getenv(p["api_key_env"], ""),
    }


def get_all_providers() -> list[dict]:
    """获取所有提供商（脱敏）列表"""
    result = []
    for pid, p in PROVIDERS.items():
        key = os.getenv(p["api_key_env"], "")
        result.append({
            "id": pid,
            "name": p["name"],
            "model": p["model"],
            "models": p.get("models", [p["model"]]),
            "has_key": bool(key),
        })
    return result


def get_provider_ids() -> list[str]:
    return list(PROVIDERS.keys())
