"""mistral profile (profiles/mistral.py)。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="mistral",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.mistral.ai/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("MISTRAL_API_KEY",),
    default_model="mistral/mistral-large-latest",
    fallback_models=(
        "mistral/mistral-large-latest",
        "mistral/mistral-small-latest",
    ),
    description="Mistral (云端, openai-compat, 需 MISTRAL_API_KEY)",
)
register(profile)
