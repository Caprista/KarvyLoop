"""Mock adapter（gateway/providers/mock.py）。

确定性、不触网——用于测试与离线开发，也是 atoms/executor 注入 mock 模型驱动循环的基础。
可脚本化：传入要 yield 的 Event 序列。
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from karvyloop.schemas import ModelDefinition, ProviderConfig

from ..events import Done, Event, TextDelta, Usage
from ..system import SystemPrompt


class MockAdapter:
    """可注入任意 api 名（测试把它挂到 'anthropic-messages' 等真实方言上）。"""

    def __init__(self, api: str = "mock", script: Optional[list[Event]] = None):
        self.api = api
        self.script = script if script is not None else [
            TextDelta("ok"), Usage(input_tokens=10, output_tokens=5), Done("end_turn"),
        ]
        self.last_request: dict | None = None      # 测试可检查传入

    async def complete(self, messages, tools, model: ModelDefinition,
                       provider: ProviderConfig, *, system: Optional[SystemPrompt] = None,
                       extra_body: Optional[dict] = None, cache: bool = True
                       ) -> AsyncIterator[Event]:
        self.last_request = {
            "messages": messages, "tools": tools, "model": model.id,
            "system_blocks": system.to_blocks(cache=cache) if system else None,
            "extra_body": extra_body,   # 推理档位等注入参数(测试可断言)
            "cache": cache,             # prompt cache 开关(测试可断言)
        }
        for ev in self.script:
            yield ev

    async def embed(self, text: str, model: ModelDefinition,
                    provider: ProviderConfig) -> list[float]:
        return [float(len(text))] * 8
