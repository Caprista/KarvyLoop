"""LoopState（atoms/loop_state.py）—— 跨迭代状态 + transition 记录。

规格：docs/modules/atom-executor.md §2.1（HR-11 可测性）。
`transition.reason` 显式记录本轮走的是哪条分支（AC10），便于测试断言。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Transition:
    """本轮主循环走过的关键决策（AC10:可断言）。"""

    reason: str  # 例: "no_tool_use", "ran_tools", "circuit_open", "max_turns", "aborted"
    extra: dict = field(default_factory=dict)


@dataclass
class LoopState:
    """单任务的可变状态（每轮整体替换以保证快照可回放）。"""

    messages: list[dict] = field(default_factory=list)
    turn_count: int = 0
    transition: Transition = field(default_factory=lambda: Transition(reason="init"))
    recovery_flags: dict = field(default_factory=dict)
    # 断路器：连续失败计数（任何工具执行失败即 +1；成功归 0）
    consecutive_failures: int = 0
    # 累计 token / 成本（hook 给上层做 blocking_limit）
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0
    cumulative_cost_usd: float = 0.0
    # 退出请求：None=未请求；True=已请求,本轮跑完即退出
    abort_requested: Optional[bool] = None

    def copy_for_next_turn(self) -> "LoopState":
        """返回新对象,内部可变列表也复制(防止后续 mutate 旧快照)。"""
        return LoopState(
            messages=[m for m in self.messages],
            turn_count=self.turn_count,
            transition=self.transition,  # 仅读
            recovery_flags=dict(self.recovery_flags),
            consecutive_failures=self.consecutive_failures,
            cumulative_input_tokens=self.cumulative_input_tokens,
            cumulative_output_tokens=self.cumulative_output_tokens,
            cumulative_cost_usd=self.cumulative_cost_usd,
            abort_requested=self.abort_requested,
        )
