"""atoms — 原子运行时：ReAct 主循环 + 读写并发分区 + 断路器

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/atom-executor.md
里程碑：M0（按需执行器）。M3：daemon 调度器。
"""

from __future__ import annotations

from .executor import (
    TerminalEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    run,
)
from .loop_state import LoopState, Transition
from .orchestration import (
    MAX_CONCURRENT,
    ToolResult,
    ToolUseBlock,
    run_tools,
)
from .terminal import Terminal

__all__ = [
    # 主循环
    "run",
    "TerminalEvent", "TextEvent", "ThinkingEvent", "ToolCallEvent", "ToolResultEvent",
    # 状态
    "LoopState", "Transition",
    # 编排
    "run_tools", "ToolUseBlock", "ToolResult", "MAX_CONCURRENT",
    # 终止
    "Terminal",
]
