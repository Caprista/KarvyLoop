"""默认 adapter 注册（gateway/providers/__init__.py）。

M0 实现：anthropic-messages（真实，集成路径）。openai/ollama/google 等暂为占位 stub，
调用时给出清晰的 NotImplementedError（诚实标注：M0 未实现）。其余靠注入 mock 测试。
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from karvyloop.schemas import ModelDefinition, ProviderConfig

from ..events import Event
from ..system import SystemPrompt
from .anthropic import AnthropicAdapter
from .base import ProviderAdapter, UnsupportedApiError
from .mock import MockAdapter
from .openai_completions import OpenAICompletionsAdapter


class _StubAdapter:
    """未实现的方言占位：调用时报错（M0 范围外，#1 §3.1 v1 推迟）。"""
    def __init__(self, api: str):
        self.api = api

    async def complete(self, *a, **k) -> AsyncIterator[Event]:
        raise NotImplementedError(f"api '{self.api}' adapter 在 M0 未实现（见 docs/modules/gateway.md §4 v1 范围）")
        yield  # pragma: no cover — 使其成为 async generator

    async def embed(self, text: str, model: ModelDefinition, provider: ProviderConfig) -> list[float]:
        raise NotImplementedError(f"api '{self.api}' embedding 在 M0 未实现")


def default_adapters() -> dict[str, ProviderAdapter]:
    adapters: dict[str, ProviderAdapter] = {
        "anthropic-messages": AnthropicAdapter(),
        "openai-completions": OpenAICompletionsAdapter(),   # P3:真 HTTP/SSE(OpenAI + 兼容端点)
    }
    for api in ("openai-responses", "google-generative-ai", "ollama", "bedrock-converse"):
        adapters[api] = _StubAdapter(api)
    return adapters


__all__ = ["ProviderAdapter", "UnsupportedApiError", "AnthropicAdapter",
           "OpenAICompletionsAdapter", "MockAdapter", "default_adapters"]
