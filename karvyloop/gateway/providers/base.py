"""Provider adapter 协议（gateway/providers/base.py）。

每个 adapter 负责：把统一请求翻成某 api 方言、流式调用、把原生输出归一化成统一 Event。
规格：docs/modules/gateway.md §3。
"""

from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from karvyloop.schemas import ModelDefinition, ProviderConfig

from ..events import Event
from ..system import SystemPrompt


@runtime_checkable
class ProviderAdapter(Protocol):
    api: str

    async def complete(self, messages: list[dict], tools: list[dict],
                       model: ModelDefinition, provider: ProviderConfig,
                       *, system: Optional[SystemPrompt] = None) -> AsyncIterator[Event]:
        ...

    async def embed(self, text: str, model: ModelDefinition,
                    provider: ProviderConfig) -> list[float]:
        ...


class UnsupportedApiError(KeyError):
    """没有为该 api 方言注册 adapter。"""
