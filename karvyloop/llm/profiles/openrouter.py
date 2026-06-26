"""openrouter profile (profiles/openrouter.py) — OpenRouter 网关。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="openrouter",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://openrouter.ai/api/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("OPENROUTER_API_KEY",),
    default_model="openrouter/anthropic/claude-sonnet-4-6",
    fallback_models=(),
    description="OpenRouter 网关 (云端, openai-compat, 需 OPENROUTER_API_KEY, 可路由多家模型)",
)
register(profile)
