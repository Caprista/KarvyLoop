"""ollama profile (profiles/ollama.py)。

本地推理，openai-completions 兼容端点（http://127.0.0.1:11434/v1）。
auth_type=none，env_vars 空（不需要 API key）。
"""
from __future__ import annotations

from karvyloop.llm.profile import (
    API_MODE_OPENAI_COMPLETIONS,
    AUTH_TYPE_NONE,
    ProviderProfile,
)
from karvyloop.llm.registry import register

profile = ProviderProfile(
    name="ollama",
    api_mode=API_MODE_OPENAI_COMPLETIONS,
    base_url="http://127.0.0.1:11434/v1",
    auth_type=AUTH_TYPE_NONE,
    auth_header="Authorization",
    env_vars=(),
    default_model="ollama/qwen2.5-coder:7b",
    fallback_models=(
        "ollama/qwen2.5-coder:7b",
        "ollama/llama3.1:8b",
        "ollama/mistral:7b",
    ),
    description="本地 Ollama (http://127.0.0.1:11434, 数据不出门)",
)
register(profile)
