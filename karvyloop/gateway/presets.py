"""gateway/presets — 引导式 onboarding 的 provider 预设(单一真理来源)。

零门槛的关键:新用户不该面对 10 个技术字段。选一个 provider → 我们预填 base_url/api/auth/
默认模型/ctx/max,他只需粘一个 key(本地模型则给安装指引)。每条带"去哪拿 key"的链接。

诚实边界:模型 id 是**可改的合理默认**(各家会更新);base_url/api/auth 形态对齐已知能跑的
配置(README anthropic + init 模板 ollama)。要接没列出的端点 → 走"高级/自定义"表单。
"""
from __future__ import annotations

# 每条:id(provider 名,与 model id 前缀一致)/ name / base_url / auth_header / api /
# 默认模型(id/name/ctx/max)/ get_key_url(去哪拿 key)/ key_env(惯例 env 名)/
# is_local(本地,免 key)/ install_hint(本地安装指引)
PROVIDER_PRESETS: list[dict] = [
    {
        "id": "anthropic", "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com", "messages_path": "/v1/messages",
        "auth_header": "x-api-key", "api": "anthropic-messages",
        "model_id": "anthropic/claude-sonnet-4-6", "model_name": "Claude Sonnet 4.6",
        "context_window": 200000, "max_tokens": 8192,
        "key_env": "ANTHROPIC_API_KEY",
        "get_key_url": "https://console.anthropic.com/settings/keys",
        "is_local": False,
    },
    {
        "id": "openai", "name": "OpenAI",
        "base_url": "https://api.openai.com/v1", "messages_path": "/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "model_id": "openai/gpt-4o", "model_name": "GPT-4o",
        "context_window": 128000, "max_tokens": 8192,
        "key_env": "OPENAI_API_KEY",
        "get_key_url": "https://platform.openai.com/api-keys",
        "is_local": False,
    },
    {
        "id": "deepseek", "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1", "messages_path": "/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "model_id": "deepseek/deepseek-chat", "model_name": "DeepSeek Chat",
        "context_window": 64000, "max_tokens": 8192,
        "key_env": "DEEPSEEK_API_KEY",
        "get_key_url": "https://platform.deepseek.com/api_keys",
        "is_local": False,
    },
    {
        "id": "kimi", "name": "Kimi / Moonshot (Global)",
        "base_url": "https://api.moonshot.ai/v1", "messages_path": "/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "model_id": "kimi/kimi-k2-0711-preview", "model_name": "Kimi K2 (Global)",
        "context_window": 128000, "max_tokens": 8192,
        "key_env": "MOONSHOT_API_KEY",
        "get_key_url": "https://platform.moonshot.ai/console/api-keys",
        "is_local": False,
    },
    {
        # Kimi 标准中国区聊天端点(moonshot.cn)—— 与 Global(moonshot.ai)是**两套账号/两把 key**,
        # 互不通用(用户实测"Kimi 跑不通"的一大来源:拿 CN 平台的 key 打 Global 端点 → 401)。
        # 端点/鉴权与 llm/profiles/kimi.py(已验通)一致:moonshot.cn/v1 + Authorization Bearer。
        "id": "kimi-cn", "name": "Kimi / Moonshot (中国区 CN)",
        "base_url": "https://api.moonshot.cn/v1", "messages_path": "/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "model_id": "kimi-cn/kimi-k2-0711-preview", "model_name": "Kimi K2 (CN)",
        "context_window": 128000, "max_tokens": 8192,
        "key_env": "MOONSHOT_API_KEY",
        "get_key_url": "https://platform.moonshot.cn/console/api-keys",
        "is_local": False,
    },
    {
        # Kimi 国区"For Coding"端点 —— 与 Global(moonshot.ai)/标准 CN 聊天(moonshot.cn)都不同。
        # 它按 User-Agent **只放行白名单内的编码客户端**(Kimi CLI / Claude Code / Roo Code 等);非白名单 → 403。
        # 重要(Kimi 明文 TOS):**篡改 UA 假装成别的客户端 = 违规,可能封停会员权益**。所以这里
        # 发 KarvyLoop **自己真实的 UA** —— 在白名单批准前会 403(预期),别用伪造 UA 绕。
        # 接入正路:开源项目把仓库 URL 提交给 Kimi onboarding 申请把本 UA 加进白名单,过审即通。
        # 仅 Forge(编码能力)该用此端点;通用对话/决策走 Global / 标准 CN 聊天 / 别家。key 形如 sk-kimi-…。
        "id": "kimi-coding", "name": "Kimi For Coding (CN, 需申请白名单)",
        "base_url": "https://api.kimi.com/coding/v1", "messages_path": "/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "extra_headers": {"User-Agent": "KarvyLoop-Forge/0.1 (+https://github.com/Caprista/KarvyLoop)"},
        "model_id": "kimi-coding/kimi-for-coding", "model_name": "Kimi For Coding",
        "context_window": 256000, "max_tokens": 8192, "reasoning": True,
        "key_env": "KIMI_API_KEY",
        "get_key_url": "https://platform.kimi.com/console/api-keys",
        "note": "Coding-only endpoint, gated to allowlisted clients. Uses KarvyLoop's real UA — will 403 until Kimi adds it to the allowlist (submit the repo URL to their client-onboarding). Do NOT spoof another client's UA (TOS violation).",
        "is_local": False,
    },
    {
        "id": "openrouter", "name": "OpenRouter (many models, one key)",
        "base_url": "https://openrouter.ai/api/v1", "messages_path": "/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "model_id": "openrouter/anthropic/claude-3.5-sonnet", "model_name": "Claude 3.5 Sonnet (via OpenRouter)",
        "context_window": 200000, "max_tokens": 8192,
        "key_env": "OPENROUTER_API_KEY",
        "get_key_url": "https://openrouter.ai/keys",
        "is_local": False,
    },
    {
        "id": "ollama", "name": "Ollama (run locally, no key)",
        "base_url": "http://127.0.0.1:11434", "messages_path": "/v1/chat/completions",
        "auth_header": "Authorization", "api": "openai-completions",
        "model_id": "ollama/qwen2.5-coder:7b", "model_name": "Qwen 2.5 Coder 7B (local)",
        "context_window": 32768, "max_tokens": 4096,
        "key_env": "",
        "get_key_url": "",
        "is_local": True,
        # 本地不是默认(弱机器体验糟),但**支持**:有需求就按这个指引装
        "install_hint": "install Ollama from https://ollama.com, then run `ollama pull qwen2.5-coder:7b`",
    },
]


def presets_public() -> list[dict]:
    """给前端的安全副本(无敏感信息;本就无 key)。"""
    return [dict(p) for p in PROVIDER_PRESETS]


def kimi_key_guidance(api_key: str, base_url: str = "") -> str:
    """Kimi 三张面孔的诚实提示:`sk-kimi-` 前缀 = **Kimi For Coding** 的 key(仅
    api.kimi.com/coding/v1,且有 UA 白名单门),粘到 moonshot Global/CN 聊天端点必 401。

    返回本地化提示串;不适用(不是 sk-kimi- key / 本就配的 coding 端点 / 遮罩串)→ ""。
    只看 key **前缀**,绝不 log/回显 key 本身。
    """
    k = str(api_key or "").strip()
    if not k.startswith("sk-kimi-"):
        return ""
    if "api.kimi.com" in str(base_url or "").lower():
        return ""   # 已经在 coding 端点上,不用提示
    from karvyloop.i18n import t
    return t("models.kimi_coding_key_hint")


__all__ = ["PROVIDER_PRESETS", "presets_public", "kimi_key_guidance"]
