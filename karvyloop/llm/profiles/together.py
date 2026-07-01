"""together profile (profiles/together.py) — Together AI 开源模型托管。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="together",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.together.xyz/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("TOGETHER_API_KEY",),
    default_model="together/meta-llama/Llama-3.3-70B-Instruct-Turbo",
    fallback_models=(),
    description="Together AI (云端, openai-compat, 需 TOGETHER_API_KEY, 托管开源模型)",
)
register(profile)
