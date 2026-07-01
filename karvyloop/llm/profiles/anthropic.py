"""anthropic profile (profiles/anthropic.py)。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_ANTHROPIC_MESSAGES,
    AUTH_TYPE_API_KEY_HEADER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="anthropic",
    api_mode=API_MODE_ANTHROPIC_MESSAGES,
    base_url="https://api.anthropic.com",
    auth_type=AUTH_TYPE_API_KEY_HEADER,
    auth_header="x-api-key",
    env_vars=("ANTHROPIC_API_KEY",),
    default_model="anthropic/claude-sonnet-4-6",
    fallback_models=(
        "anthropic/claude-sonnet-4-6",
        "anthropic/claude-opus-4-8",
        "anthropic/claude-haiku-4-5-20251001",
    ),
    description="Anthropic Claude (云端, 需 ANTHROPIC_API_KEY)",
)
register(profile)
