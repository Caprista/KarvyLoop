"""openai profile (profiles/openai.py)。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="openai",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.openai.com/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("OPENAI_API_KEY",),
    default_model="openai/gpt-4o",
    fallback_models=(
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "openai/o1",
    ),
    description="OpenAI GPT (云端, 需 OPENAI_API_KEY)",
)
register(profile)
