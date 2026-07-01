"""cognition.distill — 后台蒸馏（cognition/distill.py）。

规格：docs/modules/cognition-memory.md §3 distill.py + §4 "与 crystallize 共用循环"
- fork 一个受限 agent(工具白名单只限 memory.write / skill.observe),不污染主对话
- 同一后台循环既判记忆也判结晶(业界做法)
- pin 的记忆不可被归档
- M1 v1:review agent 的 LLM 抽取留 P1;这里做"决策应用器"
  (reviewer 决定动作 → 在 background_review 里直接 apply),并暴露接口
  让 P1 接 review_agent.ask(...)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from karvyloop.cognition.memory import MemoryManager
from karvyloop.cognition.recall import MemoryIndex
from karvyloop.cognition.trace import TraceStore
from karvyloop.schemas import AtomRun, Belief

from karvyloop.crystallize.observe import observe
from karvyloop.crystallize.store import UsageStore


# ---- 蒸馏决策动作(由 review agent 产出;M1 v1 接受外部传入)----

class ActionKind(str, Enum):
    MEMORY_WRITE = "memory"      # 写一条 Belief
    MEMORY_PIN = "memory_pin"    # pin(防归档)
    MEMORY_ARCHIVE = "memory_archive"  # 归档
    SKILL_OBSERVE = "skill"      # 触发 crystallize.observe


@dataclass
class DistillAction:
    kind: ActionKind
    # MEMORY_* 必带
    belief: Optional[Belief] = None
    pinned: bool = False
    # MEMORY_ARCHIVE 必带:要被归档的 Belief(content 匹配)
    archive_target_content: Optional[str] = None
    # SKILL_OBSERVE 必带:AtomRun 列表
    runs: Optional[list[AtomRun]] = None
    note: str = ""


@dataclass
class DistillResult:
    actions_applied: list[DistillAction]
    skipped: list[DistillAction]  # 失败的(无 provenance 等)


# ---- 受限 agent 工具白名单(只允许动 memory / skill)----
ALLOWED_TOOLS = frozenset({"memory.write", "memory.archive", "skill.observe"})


def validate_action(action: DistillAction) -> Optional[str]:
    """返回 None 表示合法;返回 str 是错误说明(spec §3 受限 agent 工具白名单)。"""
    if action.kind in (ActionKind.MEMORY_WRITE, ActionKind.MEMORY_PIN):
        if action.belief is None:
            return "MEMORY_* 需要 belief"
        if not action.belief.provenance:
            return "Belief.provenance 必填(HR-7)"
    if action.kind is ActionKind.MEMORY_ARCHIVE:
        if not action.archive_target_content:
            return "MEMORY_ARCHIVE 需要 archive_target_content"
    if action.kind is ActionKind.SKILL_OBSERVE:
        if not action.runs:
            return "SKILL_OBSERVE 需要 runs(非空 list[AtomRun])"
    return None


def apply_action(
    action: DistillAction,
    *,
    memory: MemoryManager,
    crystallize_store: UsageStore,
) -> Optional[str]:
    """应用一个动作;返回 None = 成功,str = 失败原因(供 background_review 收集 skipped)。"""
    err = validate_action(action)
    if err is not None:
        return err
    if action.kind is ActionKind.MEMORY_WRITE:
        memory.write(action.belief, pinned=action.pinned)  # type: ignore[arg-type]
    elif action.kind is ActionKind.MEMORY_PIN:
        # pin 是一条新 Belief + pinned=True(等价于 write + pinned)
        memory.write(action.belief, pinned=True)  # type: ignore[arg-type]
    elif action.kind is ActionKind.MEMORY_ARCHIVE:
        target = action.archive_target_content
        # 找匹配 content 的 Belief(去索引)
        existing = memory.index.get(target) if target else None
        if existing is not None:
            # pin 的不可被归档
            if memory.index.is_pinned(existing):
                return f"Belief 已 pin,不可归档:{target[:40]!r}"
            memory.archive(existing)   # 走 archive(remove + 落盘),否则归档不持久 → 重启复活
    elif action.kind is ActionKind.SKILL_OBSERVE:
        observe(action.runs, crystallize_store)  # type: ignore[arg-type]
    return None


# ---- 后台 review:共用循环 ----

async def background_review(
    actions: list[DistillAction],
    *,
    memory: MemoryManager,
    crystallize_store: UsageStore,
    clock=time.time,
) -> DistillResult:
    """应用一组蒸馏决策动作(由 review agent 产出)。

    M1 v1 接受外部传入 actions(把 LLM 抽取这步独立出去,P1 接上):
    - 验证白名单(任何"用错工具"的动作被跳过并记 skipped)
    - 验证后写入 memory 或 trigger crystallize.observe
    - 同一函数既管 memory 也管 skill(共用循环 spec §4)

    受限 agent 工具白名单语义:`validate_action` 实际就是白名单;`apply_action`
    内部不接触任何"非白名单"操作(没有 read_file / run_command 等)。
    """
    applied: list[DistillAction] = []
    skipped: list[DistillAction] = []
    for a in actions:
        err = apply_action(a, memory=memory, crystallize_store=crystallize_store)
        if err is None:
            applied.append(a)
        else:
            # 带上原因便于上层打日志
            a.note = err
            skipped.append(a)
    return DistillResult(actions_applied=applied, skipped=skipped)


__all__ = [
    "ActionKind", "DistillAction", "DistillResult",
    "ALLOWED_TOOLS", "validate_action", "apply_action", "background_review",
]
