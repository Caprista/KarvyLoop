"""protocols — 业务域与其他模块的协议(注入接口)。

**核心不变量**:
- D7 全部依赖注入
- D8 不调 LLM

设计:docs/18 §3.5。
"""
from __future__ import annotations

import dataclasses
from typing import Callable, Optional, Protocol


# ---- 协议接口(供 A2A 接入使用)----

class AuditChainLike(Protocol):
    """拍 5 审计链协议(留口,M3+ 接入拍 5 Auditor)。"""
    def append(self, entry: object) -> None: ...


class EnvelopeRouterLike(Protocol):
    """A2A 协议路由(docs/19 留口)。"""
    def route(self, env: object) -> object: ...


# ---- 注入式 routine 调度器(留口,M3+ 接拍 7 InstanceManager)----

RoutineCallback = Callable[[str, dict], None]


@dataclasses.dataclass(frozen=True)
class RoutineRunner:
    """Routine 运行器(纯注入式,不调 LLM,不调调度器)。

    M3 拍 1 v0:只存 callback,不主动调度。
    M3+:接拍 7 InstanceManager 调度。
    """
    callbacks: dict[str, RoutineCallback] = dataclasses.field(default_factory=dict)

    def register(self, routine_type: str, callback: RoutineCallback) -> None:
        self.callbacks[routine_type] = callback

    def run(self, routine_type: str, payload: dict) -> None:
        cb = self.callbacks.get(routine_type)
        if cb is None:
            return  # 未注册 = 跳过(不强求)
        cb(routine_type, payload)
