"""karvy — M3 路线 C 拍 3:小卡本体(全局唯一单实例的 observer 助手)。

设计:docs/20-karvy-chat.md。

**核心概念**:
- 小卡 = observer(只读,从不以自己名义参与业务)
- 单实例(K6 灵魂级,threading.Lock 双重检查)
- 代发透明(K2:from: user, by: (karvy,))
- N 原子 agent 不参与 A2A(K7)
- 工作台只读(K4)
- H2A 决策 = 用户拍板(K5)

**核心不变量**(doc §4):
- K1 小卡永远 observer
- K2 courier_send 构造 from: user, by: (karvy,)
- K3 小卡只收 BROADCAST
- K4 工作台只读
- K5 H2A = 用户拍板
- K6 小卡单实例
- K7 原子 agent 不参与 A2A
- K8 不调 LLM
"""
from __future__ import annotations

from .core import (
    KARVY,
    KARVY_ROLE,
    KarvyAlreadyInitializedError,
    KarvyCore,
    KarvyRoleError,
)
from .courier import Courier, TimestampFn
from .observer import BoardSnapshot, WorkbenchObserver, default_filter
from .atoms import (
    BoardAggregator,
    DataCourier,
    Overseer,
    TaskTracker,
    # IntentAnalyst 是小卡私有原子 agent — **不**从本 __init__ 导出
    # (docs/20 §3.10 私有纪律;任何"我也想用意图分析"请自己写)
)
from .h2a import (
    H2A_ACCEPT,
    H2A_DEFER,
    H2A_REJECT,
    H2ADecision,
    UserInputFn,
    decision_to_envelope,
    h2a_decide,
)

__all__ = [
    # core
    "KARVY",
    "KARVY_ROLE",
    "KarvyAlreadyInitializedError",
    "KarvyCore",
    "KarvyRoleError",
    # courier
    "Courier",
    "TimestampFn",
    # observer
    "BoardSnapshot",
    "WorkbenchObserver",
    "default_filter",
    # atoms(公共部分:4 类)
    "BoardAggregator",
    "DataCourier",
    "Overseer",
    "TaskTracker",
    # atoms(私有:IntentAnalyst **不**列 — 走 karvyloop.karvy.atoms.IntentAnalyst 深路径)
    # h2a
    "H2A_ACCEPT",
    "H2A_DEFER",
    "H2A_REJECT",
    "H2ADecision",
    "UserInputFn",
    "decision_to_envelope",
    "h2a_decide",
]
