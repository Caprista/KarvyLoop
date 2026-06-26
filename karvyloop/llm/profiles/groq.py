"""groq profile (profiles/groq.py)。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="groq",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.groq.com/openai/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("GROQ_API_KEY",),
    default_model="groq/llama-3.3-70b-versatile",
    fallback_models=(
        "groq/llama-3.3-70b-versatile",
        "groq/llama-3.1-8b-instant",
    ),
    description="Groq (云端, openai-compat, 需 GROQ_API_KEY)",
)
register(profile)
