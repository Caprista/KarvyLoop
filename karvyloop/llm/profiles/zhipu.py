"""zhipu profile (profiles/zhipu.py) — 智谱 GLM。

alias: glm → zhipu（行业习惯短名）。
"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="zhipu",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://open.bigmodel.cn/api/paas/v4",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("ZHIPU_API_KEY", "GLM_API_KEY", "ZAI_API_KEY"),
    default_model="zhipu/glm-4-plus",
    fallback_models=("zhipu/glm-4-plus", "zhipu/glm-4-flash"),
    description="智谱 GLM (云端, openai-compat, 需 ZHIPU_API_KEY)",
    aliases=("glm", "zai"),
)
register(profile)
