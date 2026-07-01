"""gateway — LLM 网关 + 模型注册表 + 软默认层叠解析。

规格：docs/modules/gateway.md。里程碑：M0。
公开 API：ModelRegistry / GatewayClient / ResolveScope + 统一 Event 类型。
"""

from __future__ import annotations

from .client import GatewayClient
from .cost import CostMeter
from .events import (
    Done,
    ErrorEvent,
    Event,
    TextDelta,
    ToolUseDelta,
    ToolUseStart,
    ToolUseStop,
    Usage,
)
from .providers import AnthropicAdapter, MockAdapter, default_adapters
from .providers.base import ProviderAdapter, UnsupportedApiError
from .registry import ModelRegistry, UnknownModelError
from .resolve import ResolveScope, resolve_model
from .system import SystemPrompt

__all__ = [
    "GatewayClient", "ModelRegistry", "UnknownModelError", "ResolveScope", "resolve_model",
    "SystemPrompt", "CostMeter", "UnsupportedApiError", "ProviderAdapter",
    "MockAdapter", "AnthropicAdapter", "default_adapters",
    "Event", "TextDelta", "ToolUseStart", "ToolUseDelta", "ToolUseStop",
    "Usage", "Done", "ErrorEvent",
]
