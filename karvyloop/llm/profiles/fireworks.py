"""fireworks profile (profiles/fireworks.py) — Fireworks AI 推理服务。"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_BEARER,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="fireworks",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="https://api.fireworks.ai/inference/v1",
    auth_type=AUTH_TYPE_BEARER,
    auth_header="Authorization",
    env_vars=("FIREWORKS_API_KEY",),
    default_model="fireworks/accounts/fireworks/models/llama-v3p3-70b-instruct",
    fallback_models=(),
    description="Fireworks AI (云端, openai-compat, 需 FIREWORKS_API_KEY)",
)
register(profile)
