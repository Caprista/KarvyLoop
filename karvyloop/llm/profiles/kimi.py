"""kimi profile (profiles/kimi.py) — moonshot alias（行业习惯短名）。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="kimi",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.moonshot.cn/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    default_model="kimi/moonshot-v1-128k",
    fallback_models=("kimi/moonshot-v1-128k", "kimi/moonshot-v1-32k"),
    description="Kimi (月之暗面 moonshot alias, openai-compat, 需 KIMI_API_KEY)",
)
register(profile)
