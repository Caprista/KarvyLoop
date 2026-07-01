"""minimax profile (profiles/minimax.py)。

minimax 走 anthropic-messages 兼容端点，区别：
  - base_url: https://api.MiniMax.chat/anthropic（不是 api.anthropic.com）
  - auth_header: Authorization Bearer（不是 x-api-key）
  - 协议仍 anthropic-messages（共用 AnthropicMessagesTransport）
"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_ANTHROPIC_MESSAGES,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="minimax",
    api_mode=API_MODE_ANTHROPIC_MESSAGES,
    base_url="https://api.MiniMax.chat/anthropic",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("MiniMax_API_KEY", "MiniMax_API_KEY"),
    default_model="minimax/MiniMax-M3",
    fallback_models=("minimax/MiniMax-M3",),
    description="MiniMax (云端, anthropic-messages 兼容, 需 MiniMax_API_KEY)",
)
register(profile)
