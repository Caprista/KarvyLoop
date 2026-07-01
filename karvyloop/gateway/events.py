"""统一流式事件类型（gateway/events.py）。

所有 provider 的原生流式输出都归一化成这些 Event，上层（atoms/executor、cli）
只认这一套，与 provider 无关。规格：docs/modules/gateway.md §3 events.py。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class Event:
    """统一事件基类。"""


@dataclass
class TextDelta(Event):
    text: str


@dataclass
class ThinkingDelta(Event):
    """模型 reasoning 块(MiniMax-M3 等 reasoning model 会发)。

    默认 executor 不消费(思考不外露);adapter 仍向上 yield 供调试/审计。
    """
    text: str


@dataclass
class ToolUseStart(Event):
    id: str
    name: str


@dataclass
class ToolUseDelta(Event):
    id: str
    partial_json: str


@dataclass
class ToolUseStop(Event):
    id: str
    input: dict = field(default_factory=dict)


@dataclass
class Usage(Event):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass
class Done(Event):
    stop_reason: str


@dataclass
class ErrorEvent(Event):
    kind: str
    message: str
