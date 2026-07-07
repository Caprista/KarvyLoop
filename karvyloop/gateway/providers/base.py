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

    # extra_body:按配置注入请求体顶层的增量参数(如推理强度落参,gateway/reasoning.py)。
    # cache:是否给稳定前缀(system 尾 + tools 尾)打 prompt cache 断点(models.prompt_cache)。
    # response_schema:约束解码 / 结构化输出的 JSON schema(gateway/structured.py);None=无约束。
    #   支持的方言据此把请求翻成原生结构化通道(anthropic 强制 tool-use / openai response_format)。
    # 三者都是**可选能力**:不接这些 kwarg 的 adapter 也合法 —— gateway 捕 TypeError 后剥掉重调(优雅降级)。
    async def complete(self, messages: list[dict], tools: list[dict],
                       model: ModelDefinition, provider: ProviderConfig,
                       *, system: Optional[SystemPrompt] = None,
                       extra_body: Optional[dict] = None,
                       cache: bool = True,
                       response_schema: Optional[dict] = None) -> AsyncIterator[Event]:
        ...

    async def embed(self, text: str, model: ModelDefinition,
                    provider: ProviderConfig) -> list[float]:
        ...


class UnsupportedApiError(KeyError):
    """没有为该 api 方言注册 adapter。"""
