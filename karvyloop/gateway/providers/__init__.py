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
    """未实现的方言占位：调用时报错（M0 范围外，#1 §3.1 v1 推迟）。

    CFG-04:报错必须带行动指引(i18n en+zh)——用户撞到这里时已经在聊天路径上,
    "M0 未实现"五个字没法自救;告诉他改哪、改成什么。写入侧(config_models)与验证侧
    (routes /model/validate)各有一道前置闸,这里是最后的诚实兜底。
    """
    def __init__(self, api: str):
        self.api = api

    async def complete(self, *a, **k) -> AsyncIterator[Event]:
        from karvyloop.i18n import t
        raise NotImplementedError(t("gateway.api_unimplemented", api=self.api))
        yield  # pragma: no cover — 使其成为 async generator

    async def embed(self, text: str, model: ModelDefinition, provider: ProviderConfig) -> list[float]:
        from karvyloop.i18n import t
        raise NotImplementedError(t("gateway.api_embed_unimplemented", api=self.api))


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
