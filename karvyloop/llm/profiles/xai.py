"""xai profile (profiles/xai.py) — xAI Grok。

alias: grok → xai（产品名 vs 公司名）。
"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="xai",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.x.ai/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("XAI_API_KEY",),
    default_model="xai/grok-2",
    fallback_models=("xai/grok-2", "xai/grok-2-mini"),
    description="xAI Grok (云端, openai-compat, 需 XAI_API_KEY)",
    aliases=("grok",),
)
register(profile)
