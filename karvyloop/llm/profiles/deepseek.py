"""deepseek profile (profiles/deepseek.py)。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="deepseek",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.deepseek.com/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("DEEPSEEK_API_KEY",),
    default_model="deepseek/deepseek-chat",
    fallback_models=("deepseek/deepseek-chat", "deepseek/deepseek-reasoner"),
    description="DeepSeek (云端, openai-compat, 需 DEEPSEEK_API_KEY)",
)
register(profile)
