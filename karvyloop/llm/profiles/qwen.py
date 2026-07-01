"""qwen profile (profiles/qwen.py) — 阿里 DashScope OpenAI-compat。

alias: dashscope → qwen（行业习惯短名）。
"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="qwen",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    default_model="qwen/qwen-plus",
    fallback_models=(
        "qwen/qwen-plus",
        "qwen/qwen-turbo",
        "qwen/qwen-max",
    ),
    description="阿里 Qwen/DashScope (云端, openai-compat, 需 QWEN_API_KEY 或 DASHSCOPE_API_KEY)",
    aliases=("dashscope",),
)
register(profile)
