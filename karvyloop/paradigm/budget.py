"""Paradigm Loader 的 budget + token 计数。

设计:docs/10-paradigm-loader.md §3.4 不变量 2/3(预算 + 降级顺序)。

- 默认 cap = 200_000 × 0.7 = 140_000(留 30% 给对话 + 工具结果)
- 降级:overflow → 先砍 Layer 6 → 再砍 Layer 5 → 永远不砍 Layer 0/1/2
"""

from __future__ import annotations

import dataclasses
from typing import Callable


@dataclasses.dataclass
class TokenCounter:
    """默认 token 计数器(字符数 / 4 粗估)。

    M2+ 可注入精确的 tiktoken / anthropic 计数器(本期不实现,占位即可)。
    """
    chars_per_token: int = 4

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(text) // self.chars_per_token)


@dataclasses.dataclass
class Budget:
    """token 预算追踪器。

    简单模型:每次 add 累加;over_budget() 检查;reset() 清零。
    """
    cap: int
    counter: TokenCounter
    _used: int = 0

    def add(self, text: str) -> int:
        """添加一段 text 的 token,返回本段 token 数。"""
        t = self.counter.count(text)
        self._used += t
        return t

    def used(self) -> int:
        return self._used

    def remaining(self) -> int:
        return max(0, self.cap - self._used)

    def over_budget(self) -> bool:
        return self._used > self.cap

    def reset(self) -> None:
        self._used = 0
