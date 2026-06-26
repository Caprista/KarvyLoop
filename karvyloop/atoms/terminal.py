"""原子终止原因（atoms/terminal.py）。

规格：docs/modules/atom-executor.md §2.3（HR-11 区分终止语义）。
每种独立 reason,上层可据此决定结晶/重试/告警策略。
"""

from __future__ import annotations

from enum import Enum


class Terminal(Enum):
    COMPLETED = "completed"            # 主循环正常结束(无 tool_use)
    MAX_TURNS = "max_turns"            # 超过 max_turns 上限
    CIRCUIT_OPEN = "circuit_open"      # 连续失败超断路阈值
    ABORTED_STREAMING = "aborted_streaming"   # 流式传输中被中断
    ABORTED_TOOLS = "aborted_tools"    # 工具执行阶段被中断
    HOOK_STOPPED = "hook_stopped"      # hook 强制停
    BLOCKING_LIMIT = "blocking_limit"  # token/成本预算耗尽
