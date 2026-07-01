"""gemini profile (profiles/gemini.py) — Google Gemini via OpenAI-compat 端点。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="gemini",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    default_model="gemini/gemini-2.5-pro",
    fallback_models=(
        "gemini/gemini-2.5-pro",
        "gemini/gemini-2.5-flash",
        "gemini/gemini-1.5-pro",
    ),
    description="Google Gemini (云端, openai-compat 端点, 需 GEMINI_API_KEY)",
)
register(profile)
