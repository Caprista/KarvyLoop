"""rules — 规则集(手写逻辑 / 关键词 / 正则,公共快脑工具)。

设计:docs/25-fastbrain-architecture.md §2 算法快脑。

**职责**:
- 提供"纯算法层"的快脑能力(无需 LLM / 无需学习)
- 例:正则解析 / 关键词路由 / 简单 if-then
- 命中 → 直接出(0 token)
- 不命中 → 调用方转其他快脑 / 转大脑

**纪律**:
- 公共机制 — 任何 agent / role 可调
- 0.1.0 骨架 — 留接口,实际规则在后续拍按业务补
- 规则**不**应越来越复杂(CLAUDE.md §少脚手架多信模型):复杂逻辑让 LLM 推理,规则只做"判断 + 路由"
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["Rule", "RuleRegistry", "match_first"]


class Rule:
    """单条规则(pattern + handler)。"""

    def __init__(self, name: str, pattern: re.Pattern, handler: Callable[[str], Optional[str]]) -> None:
        self.name = name
        self.pattern = pattern
        self.handler = handler

    def match(self, text: str) -> Optional[str]:
        """命中调 handler 返答案;不命中返 None。"""
        if self.pattern.search(text):
            return self.handler(text)
        return None


class RuleRegistry:
    """规则注册表(0.1.0 骨架:空表,等后续拍补具体规则)。"""

    def __init__(self) -> None:
        self._rules: list[Rule] = []

    def register(self, rule: Rule) -> None:
        self._rules.append(rule)

    def run(self, text: str) -> Optional[str]:
        """按注册顺序试所有规则,首个命中返结果。"""
        for r in self._rules:
            result = r.match(text)
            if result is not None:
                logger.debug(f"[fastbrain.rules] hit {r.name}")
                return result
        return None


def match_first(text: str, rules: list[Rule]) -> Optional[str]:
    """便捷函数:对一组规则试 match。"""
    for r in rules:
        result = r.match(text)
        if result is not None:
            return result
    return None
