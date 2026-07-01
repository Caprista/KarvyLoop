"""多轮 mock 适配器（atoms/_scripted_mock.py）—— 仅供 atoms 测试。

每次 `complete()` 调用从 `rounds` 列表中按调用顺序取一段脚本；
脚本用完 fallback 到 default_round（若提供）否则停止。
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from karvyloop.schemas import ModelDefinition, ProviderConfig

from karvyloop.gateway.events import (
    Done,
    Event,
    TextDelta,
    ToolUseStart,
    ToolUseStop,
    Usage,
)
from karvyloop.gateway.system import SystemPrompt


class ScriptedMockAdapter:
    def __init__(self, rounds: list[list[Event]], api: str = "mock",
                 default_round: Optional[list[Event]] = None):
        self.rounds = rounds
        self.api = api
        self.default_round = default_round
        self.call_count = 0
        self.last_request: dict | None = None

    async def complete(self, messages, tools, model: ModelDefinition,
                       provider: ProviderConfig, *, system: Optional[SystemPrompt] = None
                       ) -> AsyncIterator[Event]:
        if self.call_count < len(self.rounds):
            script = self.rounds[self.call_count]
        elif self.default_round is not None:
            script = self.default_round
        else:
            script = [Done("end_turn")]
        self.call_count += 1
        self.last_request = {
            "messages": messages, "tools": tools, "model": model.id,
        }
        for ev in script:
            yield ev

    async def embed(self, text, model, provider):
        return [0.0] * 4


# 一些方便构造的小工厂
def text_round(s: str) -> list[Event]:
    return [TextDelta(s), Usage(input_tokens=1, output_tokens=1), Done("end_turn")]


def tool_round(tool_id: str, name: str, inp: dict) -> list[Event]:
    """一轮：发一个 tool_use,带完整 input（在 ToolUseStop 里）。"""
    return [
        TextDelta(""),
        ToolUseStart(id=tool_id, name=name),
        ToolUseStop(id=tool_id, input=inp),
        Usage(input_tokens=2, output_tokens=2),
        Done("tool_use"),
    ]
