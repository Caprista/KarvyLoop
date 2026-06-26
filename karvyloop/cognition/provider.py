"""cognition.provider — MemoryProvider 抽象 + BuiltinProvider（cognition/provider.py）。

规格：docs/modules/cognition-memory.md §3 provider.py + §4 单外部 provider 限制
- MemoryProvider Protocol:is_available / system_prompt_block / prefetch / sync_turn / consolidate
- BuiltinProvider:markdown+grep,M1 v1 = 内存 + 子串匹配
- 单外部 provider 限制:MemoryManager 同时只接受一个非 builtin provider
"""

from __future__ import annotations

from typing import Iterable, Optional, Protocol, runtime_checkable

from karvyloop.schemas import Belief

from .fence import fence
from .recall import MemoryIndex, recall


@runtime_checkable
class MemoryProvider(Protocol):
    """Memory provider 协议。"""

    name: str  # "builtin" | "letta" | "mem0" | ...

    def is_available(self) -> bool:
        """不发网络;纯本地判可用性。M1 v1 builtin 永远 True。"""
        ...

    def system_prompt_block(self) -> str:
        """拼进 system prompt 的静态块(可空)。"""
        ...

    async def prefetch(self, query: str, *, scope: str = "personal",
                        limit: int = 10) -> list[Belief]:
        """轮前召回 → 喂给 fence()。"""
        ...

    async def sync_turn(self, user: str, assistant: str) -> None:
        """轮后异步写入(extract beliefs from turn)。M1 v1 = noop。"""
        ...

    async def consolidate(self) -> None:
        """后台蒸馏触发时调用。M1 v1 = noop(由 distill.background_review 驱动)。"""
        ...


class BuiltinProvider:
    """内置 provider:memory/personal/ 域 + 内存索引(零外部依赖)。

    spec 路径映射:personal → 'personal',domain → 'domain' + 域 ID
    实际 filesystem 落盘由 P1 阶段接;M1 v1 全部在内存。
    """

    name = "builtin"

    def __init__(self, index: MemoryIndex) -> None:
        self._index = index

    def is_available(self) -> bool:
        return True

    def system_prompt_block(self) -> str:
        # builtin 没什么要注入的;记号方便 system prompt 知道有这一路
        return ""

    async def prefetch(self, query: str, *, scope: str = "personal",
                        limit: int = 10) -> list[Belief]:
        hits = recall(query, self._index, scope=scope, limit=limit)
        return [h.belief for h in hits]

    async def sync_turn(self, user: str, assistant: str) -> None:
        # M1 v1:不主动 extract(留给 distill background_review)
        return

    async def consolidate(self) -> None:
        return


__all__ = ["MemoryProvider", "BuiltinProvider"]
