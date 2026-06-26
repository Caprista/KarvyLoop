"""4 类**调**度**场**景**灵**魂**层**子**集**。

**核心不变量**:
- M2 scenario 决**定** soul_subset
- M3 4 类**子**集**全**有**

设计:docs/17 §3.2。
"""
from __future__ import annotations

from typing import Final


# 4 类**场**景**(协**议**锁**住**)
SCENARIO_TRANSFER: Final[str] = "transfer"   # 调岗 — 7 文件
SCENARIO_WORK: Final[str] = "work"            # 上班 — 5 文件
SCENARIO_DM: Final[str] = "dm"                # 私聊 — 1 文件
SCENARIO_SHARE: Final[str] = "share"          # Share — 2 文件


class Scenario(str):
    """类型化 scenario(继承 str 便于序列化)。"""
    pass


# 4 类**子**集**(M3 协**议**锁**住**)
SOUL_SUBSETS: Final[dict[str, tuple[str, ...]]] = {
    SCENARIO_TRANSFER: (
        "IDENTITY", "SOUL", "USER", "MEMORY", "COMMITMENT", "VERIFY", "COMPOSITION",
    ),
    SCENARIO_WORK: (
        "IDENTITY", "SOUL", "USER", "COMMITMENT", "VERIFY",
    ),
    SCENARIO_DM: (
        "IDENTITY",   # 摘要即可,**不**加**载**完整
    ),
    SCENARIO_SHARE: (
        "SOUL",       # 公开部分
        "COMPOSITION",
    ),
}


def get_soul_subset(scenario: str) -> tuple[str, ...]:
    """M2 入口:scenario → soul_subset(**不**接**受** **外**部**传**)。"""
    if scenario not in SOUL_SUBSETS:
        raise ValueError(
            f"Unknown scenario: {scenario!r}; expected one of {list(SOUL_SUBSETS)}"
        )
    return SOUL_SUBSETS[scenario]
