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
    INFRA_DEAD = "infra_dead"          # 基础能力失效:网关/网络/模型解析不可用


# ---- 终止语义分类(docs/02 §15 Pursuit Loop:role 重规划 vs 不白爬阶梯)----
# infra-dead = 基础能力没了(token 调不通/网络断/模型解析失败)。这**不是 planning 问题**:
# role 重规划同一条路也没用 → 上层(尽责下属阶梯)必须**立刻 fail-loud 标 infra,不进 replan**。
# 其余非 COMPLETED 终止(MAX_TURNS/CIRCUIT_OPEN/...)= planning 不够稳 → role 可重规划。
_INFRA_DEAD: frozenset[Terminal] = frozenset({Terminal.INFRA_DEAD})


def is_infra_dead(terminal: object) -> bool:
    """是否"基础能力失效"——上层据此决定 fail-loud(不重规划)。容忍 str/None 入参。"""
    if isinstance(terminal, Terminal):
        return terminal in _INFRA_DEAD
    if isinstance(terminal, str):
        return terminal == Terminal.INFRA_DEAD.value
    return False


def is_replannable(terminal: object) -> bool:
    """atom 没跑成、但属于"planning 不够稳"可由 role 重规划的那类(非 infra、非正常完成)。"""
    if isinstance(terminal, str):
        try:
            terminal = Terminal(terminal)
        except ValueError:
            return False
    if not isinstance(terminal, Terminal):
        return False
    return terminal not in _INFRA_DEAD and terminal != Terminal.COMPLETED
