"""Token 预算 + 断路器状态（context/budget.py）。

规格：docs/modules/context-governance.md §3 budget.py + §4 HR-3。
- 工具计费 token 估算（4 字符/token 粗估,M0 不接 tiktoken）
- 断路器：连续失败 N 次停手（HR-3 防"自动重试变死循环"）
- 预算：每任务 token/成本/超时上限
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


# ---- 断路器阈值(HR-3;CC 血泪:曾连续失败 3272 次)----
MAX_CONSECUTIVE_FAILURES = 3

# AutoCompact 缓冲(spec §3):窗口 - 13k 留摘要生成空间
AUTOCOMPACT_BUFFER_TOKENS = 13_000

# Microcompact 触发缓冲:窗口 - 1.5k 留最新工具结果
MICROCOMPACT_BUFFER_TOKENS = 1_500

# 手动压缩缓冲(关自动压缩时留给用户空间)
MANUAL_COMPACT_BUFFER_TOKENS = 3_000

# Token 估算常数
_CHARS_PER_TOKEN = 4


def count_tokens_text(s: str) -> int:
    """粗估字符串的 token 数(M0:4 字符/token)。"""
    return max(1, len(s) // _CHARS_PER_TOKEN)


def count_tokens_messages(messages: list[dict]) -> int:
    """粗估一组消息的 token 数。

    按 OpenAI/Claude 风格消息:role + content(字符串/块列表)。
    不接 tiktoken(M0 范围内,工程稳健优先)。
    """
    total = 0
    for m in messages:
        total += 4  # role 标记 + 元数据
        c = m.get("content", "")
        if isinstance(c, str):
            total += count_tokens_text(c)
        elif isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    if "text" in blk:
                        total += count_tokens_text(str(blk["text"]))
                    else:
                        total += 8  # tool_use / tool_result 等块
        total += 4  # 闭合
    return total


@dataclass
class GovState:
    """治理状态(per-task):断路器 + 累计用量。"""

    # 断路器
    consecutive_failures: int = 0
    breaker_open: bool = False  # 显式开(open == True 时不再尝试)
    # 用量累计
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    started_at: float = field(default_factory=time.time)
    # 摘要缓存:避免重复摘要同一段(middle 段 hash → summary)
    summary_cache: dict[str, str] = field(default_factory=dict)
    # 最近一次治理时间
    last_governed_at: float = 0.0

    def record_success(self) -> None:
        """autocompact 成功 → 失败计数归零(不断开)。"""
        self.consecutive_failures = 0
        self.breaker_open = False

    def record_failure(self) -> None:
        """autocompact 失败 → 计数 +1;达 MAX 直接开断路器(HR-3)。"""
        self.consecutive_failures += 1
        if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self.breaker_open = True

    def can_attempt(self) -> bool:
        """断路器未开 + 失败次数未达上限。"""
        return not self.breaker_open and self.consecutive_failures < MAX_CONSECUTIVE_FAILURES


@dataclass
class GovConfig:
    """治理配置(per-task 或全局默认)。"""

    # 工具结果总预算(单条默认上限由 tool 决定;这里控总和)
    tool_result_budget: int = 50_000
    # microcompact 保留最近 N 个工具结果
    keep_recent_tool_results: int = 5
    # autocompact 触发阈值(覆盖默认)
    autocompact_buffer: int = AUTOCOMPACT_BUFFER_TOKENS
    # 任务级 token/成本/超时上限
    max_total_tokens: int = 1_000_000
    max_cost_usd: float = 5.0
    max_wallclock_s: float = 1800.0  # 30 分钟
    # 关自动压缩(留手动空间 → 超限抛 BlockingLimitError)
    autocompact_enabled: bool = True


@dataclass
class BlockingLimitError(Exception):
    """任务超限且 auto-compact 关闭时抛(spec §3 autocompact + §4 AC7)。"""
    code: int = 7
    reason: str = ""

    def __init__(self, reason: str = "", code: int = 7):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def autocompact_threshold(context_window: int) -> int:
    """autocompact 触发阈值 = 窗口 - 13k(spec §3 + AC2)。"""
    return max(0, context_window - AUTOCOMPACT_BUFFER_TOKENS)


def microcompact_threshold(context_window: int) -> int:
    """microcompact 触发阈值 = 窗口 - 1.5k(留最近工具结果)。"""
    return max(0, context_window - MICROCOMPACT_BUFFER_TOKENS)


__all__ = [
    "MAX_CONSECUTIVE_FAILURES",
    "AUTOCOMPACT_BUFFER_TOKENS",
    "MICROCOMPACT_BUFFER_TOKENS",
    "MANUAL_COMPACT_BUFFER_TOKENS",
    "GovState",
    "GovConfig",
    "BlockingLimitError",
    "count_tokens_text",
    "count_tokens_messages",
    "autocompact_threshold",
    "microcompact_threshold",
]
