"""moonshot profile (profiles/moonshot.py) — 月之暗面 Kimi / Moonshot。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="moonshot",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.moonshot.cn/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("MOONSHOT_API_KEY",),
    default_model="moonshot/moonshot-v1-128k",
    fallback_models=(
        "moonshot/moonshot-v1-128k",
        "moonshot/moonshot-v1-32k",
        "moonshot/moonshot-v1-8k",
    ),
    description="月之暗面 Moonshot/Kimi (云端, openai-compat, 需 MOONSHOT_API_KEY)",
)
register(profile)
